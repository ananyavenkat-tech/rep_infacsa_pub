"""
hana_calcview_to_cdgc_csv_v7.py
================================
Parses SAP HANA Calculation View XML files (.hdbcalculationview) and generates
a ZIP matching the Informatica CDGC custom scanner import format.

Metamodel architecture
----------------------
  Base objects (schemas, physical tables, columns) → native CDGC core classes
  marked as Reference so CDGC resolves them against existing catalog objects:

    Connection root → core.Resource     (core.reference = TRUE)
    Schema/DB       → core.DataSource   (core.Reference = TRUE)
    Physical table  → core.DataSet      (core.Reference = TRUE)
    Table column    → core.DataElement  (core.Reference = TRUE)

  Internal HANA calc-view execution objects → custom metamodel:

    custom.sap.hana.calscript.v7.HanaCalcView        (extends core.DataSet)
    custom.sap.hana.calscript.v7.HanaCalcViewField   (extends core.DataElement)
    custom.sap.hana.calscript.v7.HanaScriptBlock     (extends core.DataSet, core.parent = HanaCalcView)

  Lineage associations:
    Object-level : core.DataSetDataFlow        (table → ScriptBlock → HanaCalcView)
    Column-level : core.DirectionalDataFlow    (DataElement → HanaCalcViewField)

CSV files produced inside the ZIP
-----------------------------------
  core.Resource.csv
  core.DataSource.csv
  core.DataSet.csv
  core.DataElement.csv
  custom.sap.hana.calscript.v7.HanaCalcView.csv
  custom.sap.hana.calscript.v7.HanaCalcViewField.csv
  custom.sap.hana.calscript.v7.HanaScriptBlock.csv
  links.csv

Usage
-----
  python scripts/hana_calcview_to_cdgc_csv_v7.py                 # reads input/, writes output/
  python scripts/hana_calcview_to_cdgc_csv_v7.py path/to/dir
  python scripts/hana_calcview_to_cdgc_csv_v7.py path/to/file.hdbcalculationview
  HANA_SCRIPTS_DIR=./input python scripts/hana_calcview_to_cdgc_csv_v7.py
"""

import csv
import glob
import io
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_dotenv(env_path) -> None:
    p = Path(env_path)
    if not p.is_file():
        return
    with p.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

_load_dotenv(Path(__file__).parent / ".env")

SCRIPTS_DIR = os.getenv("HANA_SCRIPTS_DIR", str(Path(__file__).parent.parent / "input"))
OUTPUT_ZIP  = os.getenv("CSV_OUTPUT_ZIP",   str(Path(__file__).parent.parent / "output" / "hana_cdgc_import_v7.zip"))

# Custom metamodel package name — must match packageName in the registered model JSON
CUSTOM_PKG = "custom.sap.hana.calscript.v7"

# Synthetic root resource externalId — groups all reference connection objects
ROOT_RESOURCE_EID = "hana_ref_connection"

# Synthetic DataSource that parents all HanaCalcView objects.
# HanaCalcView extends core.DataSet, so CDGC requires a core.DataSource parent.
CALCVIEW_CONTAINER_EID  = "hana_calcviews_container"
CALCVIEW_CONTAINER_NAME = "HANA Calculation Views"

# XML namespace
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

NODE_TYPE_MAP = {
    "JoinView":        "JOIN",
    "ProjectionView":  "PROJECTION",
    "AggregationView": "AGGREGATION",
    "UnionView":       "UNION",
    "RankView":        "RANK",
}

JOIN_TYPE_MAP = {
    "inner":       "INNER",
    "leftOuter":   "LEFT_OUTER",
    "rightOuter":  "RIGHT_OUTER",
    "fullOuter":   "FULL_OUTER",
    "cross":       "CROSS",
    "text":        "TEXT",
    "referential": "REFERENTIAL",
}

# ---------------------------------------------------------------------------
# CSV column layouts
# ---------------------------------------------------------------------------

CORE_RESOURCE_FIELDS    = ["core.externalId", "core.name", "core.reference", "core.assignable"]
CORE_DATASOURCE_FIELDS  = ["core.externalId", "core.Reference", "core.assignable", "core.name"]
CORE_DATASET_FIELDS     = ["core.externalId", "core.Reference", "core.assignable", "core.name"]
CORE_DATAELEMENT_FIELDS = ["core.externalId", "core.Reference", "core.assignable", "core.name"]

CUSTOM_CALCVIEW_FIELDS = [
    "core.externalId",
    "core.name",
    "core.description",
    "core.assignable",
    f"{CUSTOM_PKG}.packagePath",
    f"{CUSTOM_PKG}.calcViewType",
    f"{CUSTOM_PKG}.defaultClient",
]

CUSTOM_CALCVIEWFIELD_FIELDS = [
    "core.externalId",
    "core.name",
    "core.description",
    "core.assignable",
    f"{CUSTOM_PKG}.keyAttribute",
    f"{CUSTOM_PKG}.columnExpression",
    f"{CUSTOM_PKG}.columnDataType",
]

CUSTOM_SCRIPTBLOCK_FIELDS = [
    "core.externalId",
    "core.name",
    "core.parent",
    "core.assignable",
    f"{CUSTOM_PKG}.scriptType",
    f"{CUSTOM_PKG}.transformationLogic",
]

LINKS_FIELDS = ["Source", "Target", "Association"]

# ---------------------------------------------------------------------------
# Association names — core structural + lineage
# ---------------------------------------------------------------------------

ASSOC_RESOURCE_PARENT_CHILD       = "core.ResourceParentChild"
ASSOC_DATASOURCE_PARENT_CHILD     = "core.DataSourceParentChild"
ASSOC_DATASET_ELEMENT_PARENTSHIP  = "core.DataSetToDataElementParentship"
ASSOC_DATASET_DATAFLOW            = "core.DataSetDataFlow"
ASSOC_DIRECTIONAL_DATAFLOW        = "core.DirectionalDataFlow"

ASSOC_CALCVIEW_TO_FIELD       = f"{CUSTOM_PKG}.HanaCalcViewToHanaCalcViewField"
ASSOC_CALCVIEW_TO_SCRIPTBLOCK = f"{CUSTOM_PKG}.HanaCalcViewToHanaScriptBlock"


# ===========================================================================
# 1. File discovery
# ===========================================================================

def find_scripts(source: str) -> list:
    p = Path(source)
    if p.is_file():
        return [str(p)]
    if p.is_dir():
        hits = sorted(glob.glob(str(p / "*.hdbcalculationview")))
        if not hits:
            raise FileNotFoundError(f"No *.hdbcalculationview files found in: {p}")
        return hits
    raise FileNotFoundError(f"Path not found: {source}")


# ===========================================================================
# 2. XML parsing
# ===========================================================================

def _node_script_type(xsi_type: str) -> str:
    suffix = xsi_type.split(":")[-1] if ":" in xsi_type else xsi_type
    return NODE_TYPE_MAP.get(suffix, "SCRIPT_BASED")


# Matches "SCHEMA"."TABLE" (plain or HTML-entity-escaped) in SQL bodies
_SQL_TABLE_RE = re.compile(
    r'"([^"]+)"\s*\.\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))'
)
_SQL_TABLE_ENT_RE = re.compile(
    r'&quot;([^&]+)&quot;\s*\.\s*(?:&quot;([^&]+)&quot;|([A-Za-z_][A-Za-z0-9_]*))'
)

# Matches CTE names: "WITH <name> AS (" or ", <name> AS ("
_CTE_NAME_RE = re.compile(r'(?:WITH|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(', re.IGNORECASE)

# Matches SQL alias patterns: "... AS <alias>" (table-level aliases)
_SQL_ALIAS_RE = re.compile(r'\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b', re.IGNORECASE)


def _extract_sql_tables(sql: str) -> list:
    """Return list of {id, schema, table} dicts from inline SQL.

    Filters out:
    - Any schema that is itself a table in another pair (alias.column confusion)
    - CTE names used as schema identifiers
    - SQL-keyword false positives
    """
    raw_pairs = []
    for pattern in (_SQL_TABLE_RE, _SQL_TABLE_ENT_RE):
        for m in pattern.finditer(sql):
            schema = m.group(1)
            table  = m.group(2) or m.group(3)
            if schema and table:
                raw_pairs.append((schema, table))

    # Collect identifiers that are real table names (right-hand side)
    real_table_names = {table for _, table in raw_pairs}

    # Collect CTE names — these are virtual, not physical tables
    cte_names = set(_CTE_NAME_RE.findall(sql))

    seen: set = set()
    results = []
    for schema, table in raw_pairs:
        if schema in real_table_names:
            continue
        if schema in cte_names or table in cte_names:
            continue
        key = (schema, table)
        if key not in seen:
            seen.add(key)
            results.append({"id": table, "schema": schema, "table": table})
    return results


def _extract_cte_blocks(sql: str) -> list:
    """Extract CTE names from a SQL body for use as HanaScriptBlock nodes."""
    return list(dict.fromkeys(_CTE_NAME_RE.findall(sql)))


# Matches plain column references in SELECT lists: ALIAS.COLUMN or bare COLUMN
# Used ONLY within a CTE body after physical tables are already known.
_ALIAS_COL_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b')


def _extract_columns_per_table(sql: str, physical_tables: list) -> dict:
    """
    Return {(schema, table): {column, ...}} extracted from inline SQL.

    Strategy:
    1. Build alias→(schema,table) map from "FROM/JOIN schema.table [AS] alias" patterns.
    2. Also treat the table name itself as an alias (BSEG.GJAHR → BSEG).
    3. Scan alias.column pairs; skip aliases that are CTE names, SQL keywords,
       or functions.
    4. Also collect bare column names from SELECT lists that are not aliases.
    """
    table_set   = {t["table"] for t in physical_tables}
    schema_set  = {t["schema"] for t in physical_tables}
    cte_names   = set(_CTE_NAME_RE.findall(sql))

    # Build alias → (schema, table) map
    alias_map: dict = {}
    for t in physical_tables:
        alias_map[t["table"]] = (t["schema"], t["table"])

    # FROM/JOIN "SCHEMA"."TABLE" [AS] alias  or  FROM "SCHEMA"."TABLE" alias
    _FROM_ALIAS_RE = re.compile(
        r'(?:FROM|JOIN)\s+'
        r'(?:"([^"]+)"|&quot;([^&]+)&quot;)\s*\.\s*'
        r'(?:"([^"]+)"|&quot;([^&]+)&quot;|([A-Za-z_]\w*))\s*'
        r'(?:AS\s+)?([A-Za-z_]\w*)',
        re.IGNORECASE,
    )
    for m in _FROM_ALIAS_RE.finditer(sql):
        schema = m.group(1) or m.group(2)
        table  = m.group(3) or m.group(4) or m.group(5)
        alias  = m.group(6)
        if schema and table and alias and alias.upper() not in ('WHERE', 'ON', 'SET'):
            alias_map[alias] = (schema, table)
            alias_map[alias.upper()] = (schema, table)

    # Collect alias.column pairs
    SQL_KEYWORDS = {
        'SELECT','FROM','WHERE','JOIN','ON','AND','OR','NOT','IN','IS','NULL',
        'CASE','WHEN','THEN','ELSE','END','AS','BY','GROUP','ORDER','HAVING',
        'LEFT','RIGHT','INNER','OUTER','FULL','CROSS','DISTINCT','WITH',
        'INSERT','UPDATE','DELETE','INTO','VALUES','SET','LIKE','BETWEEN',
        'EXISTS','UNION','ALL','LIMIT','OFFSET','TOP',
    }

    result: dict = {}
    for m in _ALIAS_COL_RE.finditer(sql):
        alias  = m.group(1)
        column = m.group(2)
        if alias.upper() in SQL_KEYWORDS or column.upper() in SQL_KEYWORDS:
            continue
        if alias in cte_names or alias.upper() in cte_names:
            continue
        key = alias_map.get(alias) or alias_map.get(alias.upper())
        if key:
            result.setdefault(key, set()).add(column)

    return result


def _extract_cte_body_balanced(sql: str, cte_name: str) -> str:
    """Return the text inside the parentheses of a named CTE using balanced-paren matching."""
    pattern = re.compile(
        r'(?:WITH|,)\s+' + re.escape(cte_name) + r'\s+AS\s*\(',
        re.IGNORECASE,
    )
    m = pattern.search(sql)
    if not m:
        return ""
    start = m.end()
    depth = 1
    pos   = start
    while pos < len(sql) and depth > 0:
        if sql[pos] == '(':
            depth += 1
        elif sql[pos] == ')':
            depth -= 1
        pos += 1
    return sql[start : pos - 1].strip()


def _build_cte_dependency_map(sql: str, cte_names: list, data_sources: list) -> dict:
    """
    Scoped per-CTE dependency analysis.

    For each CTE, inspect ONLY that CTE's own body text to find:
      - Physical tables referenced directly  (via "SCHEMA"."TABLE" patterns)
      - Prior CTE names referenced           (word-boundary match against earlier CTE names)

    Returns {cte_name: {"tables": [(schema, table), ...], "prior_ctes": [cte_name, ...]}}

    This prevents the global-regex bug where all tables were incorrectly attributed
    to the first CTE.
    """
    phys_tables = {
        (ds["schema"], ds["table"])
        for ds in data_sources
        if ds.get("schema") and ds.get("table")
    }
    result: dict = {}
    for idx, cte_name in enumerate(cte_names):
        body = _extract_cte_body_balanced(sql, cte_name)

        # Physical tables directly referenced inside this CTE's body
        scoped_tables: list = []
        seen_keys: set = set()
        for pat in (_SQL_TABLE_RE, _SQL_TABLE_ENT_RE):
            for m in pat.finditer(body):
                schema = m.group(1)
                table  = m.group(2) or m.group(3)
                key    = (schema, table)
                if key in phys_tables and key not in seen_keys:
                    seen_keys.add(key)
                    scoped_tables.append(key)

        # Prior CTEs whose names appear as identifiers in this body
        prior_ctes: list = []
        for prior_cte in cte_names[:idx]:
            if re.search(r'\b' + re.escape(prior_cte) + r'\b', body, re.IGNORECASE):
                prior_ctes.append(prior_cte)

        result[cte_name] = {"tables": scoped_tables, "prior_ctes": prior_ctes}
    return result


def _extract_column_lineage(sql: str, output_fields: list) -> dict:
    """
    Produce a best-effort column-to-CTE-block mapping.

    Returns: {output_field_name: [cte_name, ...]} where the field name appears
    in the SELECT list of a CTE block.

    Strategy: scan each CTE body for output field names; if found, record that
    the field flows through that CTE.  The final SELECT * FROM cteN is treated
    as passthrough from the last CTE to the view output.
    """
    field_names = {f["id"] for f in output_fields}

    # Split SQL into CTE segments: find each CTE body
    # Pattern: find "<cte_name> AS (" and extract up to the matching ")"
    cte_field_map: dict = {f: [] for f in field_names}

    cte_pattern = re.compile(
        r'(?:WITH|,)\s+([A-Za-z_][A-Za-z0-9_]*)\s+AS\s*\(',
        re.IGNORECASE
    )

    positions = [(m.group(1), m.end()) for m in cte_pattern.finditer(sql)]
    # Build CTE bodies by slicing between start positions
    for i, (cte_name, body_start) in enumerate(positions):
        body_end = positions[i + 1][1] if i + 1 < len(positions) else len(sql)
        body = sql[body_start:body_end]
        for fname in field_names:
            # Look for the field name as a standalone identifier in the CTE body
            if re.search(r'\b' + re.escape(fname) + r'\b', body, re.IGNORECASE):
                if cte_name not in cte_field_map[fname]:
                    cte_field_map[fname].append(cte_name)

    return cte_field_map


def parse_calcview(path: str) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()

    view_id    = root.get("id", Path(path).stem)
    package    = root.get("package", "")
    calc_type  = root.get("dataCategory", "CUBE")
    def_client = root.get("defaultClient", "")

    desc_elem   = root.find("descriptions")
    description = desc_elem.get("defaultDescription", "") if desc_elem is not None else ""

    # Explicit <DataSource> elements (graphical calc views)
    data_sources: list = []
    for ds in root.findall(".//DataSource"):
        ds_id = ds.get("id", "")
        res   = ds.find("resourceUri")
        uri   = res.text.strip() if (res is not None and res.text) else ""
        schema, _, table = uri.partition("/")
        data_sources.append({"id": ds_id, "schema": schema, "table": table})

    ds_ids = {ds["id"] for ds in data_sources}

    nodes: list = []
    script_based_tables: list = []
    sql_body_global = ""

    for node_elem in root.findall(".//calculationView"):
        xsi_type    = node_elem.get(f"{{{XSI_NS}}}type", "")
        node_id     = node_elem.get("id", "")
        stype       = _node_script_type(xsi_type)
        jt_raw      = node_elem.get("joinType", "")
        join_type   = JOIN_TYPE_MAP.get(jt_raw.lower(), "") if stype == "JOIN" else ""
        filter_elem = node_elem.find("filter")
        filter_cond = (filter_elem.text or "").strip() if filter_elem is not None else ""

        # Calculated view attributes (expressions on non-script nodes)
        calc_exprs = []
        for cva in node_elem.findall(".//calculatedViewAttribute"):
            attr_id = cva.get("id", "")
            km      = cva.find("keyMapping")
            expr    = km.get("columnName", "") if km is not None else ""
            if attr_id:
                calc_exprs.append((attr_id, expr))

        # Direct table inputs (graphical nodes only)
        table_inputs = [
            i.get("node", "").lstrip("#")
            for i in node_elem.findall("input")
            if i.get("node", "").lstrip("#") in ds_ids
        ]

        sql_body = ""
        view_attr_types: dict = {}
        cte_names: list = []

        if stype == "SCRIPT_BASED":
            def_elem = node_elem.find("definition")
            if def_elem is not None and def_elem.text:
                sql_body = def_elem.text.strip()
                sql_body_global = sql_body
                sql_tables = _extract_sql_tables(sql_body)
                script_based_tables.extend(sql_tables)
                table_inputs = [t["id"] for t in sql_tables]
                cte_names = _extract_cte_blocks(sql_body)
                # dependency map is built after data_sources is finalised (below)

            for va in node_elem.findall(".//viewAttribute"):
                va_id = va.get("id", "")
                va_dt = va.get("datatype", "")
                if va_id:
                    view_attr_types[va_id] = va_dt

        nodes.append({
            "node_id":          node_id,
            "script_type":      stype,
            "join_type":        join_type,
            "filter_condition": filter_cond,
            "calc_expressions": calc_exprs,
            "table_inputs":     table_inputs,
            "sql_body":         sql_body,
            "view_attr_types":  view_attr_types,
            "cte_names":        cte_names,
        })

    # Merge SQL-extracted tables (deduplicated)
    existing_keys = {(ds["schema"], ds["table"]) for ds in data_sources}
    for t in script_based_tables:
        key = (t["schema"], t["table"])
        if key not in existing_keys:
            existing_keys.add(key)
            data_sources.append(t)

    # Enrich output fields with data types from viewAttribute declarations
    combined_attr_types: dict = {}
    for node in nodes:
        combined_attr_types.update(node.get("view_attr_types", {}))

    output_fields: list = []
    lm = root.find("logicalModel")
    if lm is not None:
        for attr in lm.findall(".//attribute"):
            fid = attr.get("id", "")
            output_fields.append({
                "id":            fid,
                "key_attribute": "true",
                "data_type":     combined_attr_types.get(fid, ""),
            })
        for measure in lm.findall(".//measure"):
            fid = measure.get("id", "")
            output_fields.append({
                "id":            fid,
                "key_attribute": "false",
                "data_type":     combined_attr_types.get(fid, ""),
            })

    # Column lineage: which CTE blocks each output field passes through
    col_lineage: dict = {}
    if sql_body_global and output_fields:
        col_lineage = _extract_column_lineage(sql_body_global, output_fields)

    # Physical columns per table extracted from SQL body
    table_columns: dict = {}
    if sql_body_global and data_sources:
        table_columns = _extract_columns_per_table(sql_body_global, data_sources)

    # Scoped CTE dependency map (built after data_sources is fully merged)
    cte_dep_map: dict = {}
    for node in nodes:
        if node["script_type"] == "SCRIPT_BASED" and node.get("cte_names") and node.get("sql_body"):
            cte_dep_map = _build_cte_dependency_map(
                node["sql_body"], node["cte_names"], data_sources
            )

    return {
        "view_id":        view_id,
        "package":        package,
        "description":    description,
        "calc_view_type": calc_type,
        "default_client": def_client,
        "data_sources":   data_sources,
        "nodes":          nodes,
        "output_fields":  output_fields,
        "col_lineage":    col_lineage,
        "table_columns":  table_columns,
        "cte_dep_map":    cte_dep_map,
    }


# ===========================================================================
# 3. External ID builders
# ===========================================================================

def _core_resource_eid() -> str:
    return ROOT_RESOURCE_EID


def _core_datasource_eid(schema: str) -> str:
    """core.DataSource externalId for a HANA schema."""
    return f"hana_ref/{schema}"


def _core_dataset_eid(schema: str, table: str) -> str:
    """core.DataSet externalId for a physical table."""
    return f"hana_ref/{schema}/{table}"


def _core_dataelement_eid(schema: str, table: str, column: str) -> str:
    """core.DataElement externalId for a physical table column."""
    return f"hana_ref/{schema}/{table}/{column}"


def _custom_calcview_eid(view_id: str) -> str:
    return f"{CUSTOM_PKG}.HanaCalcView/{view_id}"


def _custom_calcviewfield_eid(view_id: str, field_id: str) -> str:
    return f"{CUSTOM_PKG}.HanaCalcViewField/{view_id}/{field_id}"


def _custom_scriptblock_eid(view_id: str, block_name: str) -> str:
    return f"{CUSTOM_PKG}.HanaScriptBlock/{view_id}/{block_name}"


# ===========================================================================
# 4. Build core reference rows
# ===========================================================================

def _empty(fields: list) -> dict:
    return {f: "" for f in fields}


def _schema_resource_eid(schema: str) -> str:
    return f"REF_HANA_{schema}_CONN"


def _schema_datasource_eid(schema: str) -> str:
    return f"REF_HANA_{schema}_DS"


def build_core_resource(parsed_views: list) -> list:
    """One core.Resource per unique HANA schema — appears in the lineage graph
    as a Reference connection node (gray Reference badge in CDGC)."""
    rows, seen = [], set()
    for v in parsed_views:
        for ds in v["data_sources"]:
            schema = ds["schema"]
            if not schema or schema in seen:
                continue
            seen.add(schema)
            row = _empty(CORE_RESOURCE_FIELDS)
            row["core.externalId"] = _schema_resource_eid(schema)
            row["core.name"]       = f"{schema}_Connection"
            row["core.reference"]  = "True"
            row["core.assignable"] = "True"
            rows.append(row)
    return rows


def build_core_datasources(parsed_views: list) -> list:
    """One core.DataSource per unique HANA schema (Reference=True) plus one
    synthetic DataSource that parents all HanaCalcView objects."""
    rows, seen = [], set()

    # Synthetic DataSource for HanaCalcView objects
    container = _empty(CORE_DATASOURCE_FIELDS)
    container["core.externalId"] = CALCVIEW_CONTAINER_EID
    container["core.Reference"]  = "False"
    container["core.assignable"] = "True"
    container["core.name"]       = CALCVIEW_CONTAINER_NAME
    rows.append(container)

    for v in parsed_views:
        for ds in v["data_sources"]:
            schema = ds["schema"]
            if not schema or schema in seen:
                continue
            seen.add(schema)
            row = _empty(CORE_DATASOURCE_FIELDS)
            row["core.externalId"] = _schema_datasource_eid(schema)
            row["core.Reference"]  = "True"
            row["core.assignable"] = "True"
            row["core.name"]       = schema
            rows.append(row)
    return rows


def build_core_datasets(parsed_views: list) -> list:
    """One core.DataSet per unique physical table — Reference=True."""
    rows, seen = [], set()
    for v in parsed_views:
        for ds in v["data_sources"]:
            key = (ds["schema"], ds["table"])
            if not ds["schema"] or not ds["table"] or key in seen:
                continue
            seen.add(key)
            row = _empty(CORE_DATASET_FIELDS)
            # externalId matches the DataSource externalId prefix for resolution
            row["core.externalId"] = f"{_schema_datasource_eid(ds['schema'])}/{ds['table']}"
            row["core.Reference"]  = "True"
            row["core.assignable"] = "True"
            row["core.name"]       = ds["table"]
            rows.append(row)
    return rows


def build_core_dataelements(parsed_views: list) -> list:
    """One core.DataElement per unique physical column — Reference=True."""
    rows, seen = [], set()
    for v in parsed_views:
        for (schema, table), columns in v.get("table_columns", {}).items():
            for col in sorted(columns):
                key = (schema, table, col)
                if key in seen:
                    continue
                seen.add(key)
                row = _empty(CORE_DATAELEMENT_FIELDS)
                row["core.externalId"] = f"{_schema_datasource_eid(schema)}/{table}/{col}"
                row["core.Reference"]  = "True"
                row["core.assignable"] = "True"
                row["core.name"]       = col
                rows.append(row)
    return rows


# ===========================================================================
# 5. Build custom metamodel rows
# ===========================================================================

def build_custom_calcviews(parsed_views: list) -> list:
    """HanaCalcView — final DataSetDataFlow target, assignable=True."""
    rows = []
    for v in parsed_views:
        row = _empty(CUSTOM_CALCVIEW_FIELDS)
        row["core.externalId"]            = _custom_calcview_eid(v["view_id"])
        row["core.name"]                  = v["view_id"]
        row["core.description"]           = v["description"]
        row["core.assignable"]            = "True"
        row[f"{CUSTOM_PKG}.packagePath"]  = v["package"]
        row[f"{CUSTOM_PKG}.calcViewType"] = v["calc_view_type"]
        row[f"{CUSTOM_PKG}.defaultClient"]= v["default_client"]
        rows.append(row)
    return rows


def build_custom_calcview_fields(parsed_views: list) -> list:
    """HanaCalcViewField — final DirectionalDataFlow target, assignable=True."""
    rows = []
    for v in parsed_views:
        for field in v["output_fields"]:
            row = _empty(CUSTOM_CALCVIEWFIELD_FIELDS)
            row["core.externalId"]               = _custom_calcviewfield_eid(v["view_id"], field["id"])
            row["core.name"]                     = field["id"]
            row["core.description"]              = ""
            row["core.assignable"]               = "True"
            row[f"{CUSTOM_PKG}.keyAttribute"]    = field["key_attribute"]
            row[f"{CUSTOM_PKG}.columnExpression"]= ""
            row[f"{CUSTOM_PKG}.columnDataType"]  = field.get("data_type", "")
            rows.append(row)
    return rows


def build_custom_scriptblocks(parsed_views: list) -> list:
    """HanaScriptBlock — intermediate DataSetDataFlow node (one per SQL CTE)."""
    rows = []
    for v in parsed_views:
        for node in v["nodes"]:
            if node["script_type"] == "SCRIPT_BASED":
                cte_names = node.get("cte_names", [])
                sql_body  = node.get("sql_body", "")
                blocks    = cte_names if cte_names else [node["node_id"]]
                for cte in blocks:
                    row = _empty(CUSTOM_SCRIPTBLOCK_FIELDS)
                    row["core.externalId"]                   = _custom_scriptblock_eid(v["view_id"], cte)
                    row["core.name"]                         = cte
                    row["core.parent"]                       = _custom_calcview_eid(v["view_id"])
                    row["core.assignable"]                   = "True"
                    row[f"{CUSTOM_PKG}.scriptType"]          = "SCRIPT_BASED"
                    row[f"{CUSTOM_PKG}.transformationLogic"] = _extract_cte_body_balanced(sql_body, cte)
                    rows.append(row)
            else:
                row = _empty(CUSTOM_SCRIPTBLOCK_FIELDS)
                row["core.externalId"]          = _custom_scriptblock_eid(v["view_id"], node["node_id"])
                row["core.name"]                = node["node_id"]
                row["core.parent"]              = _custom_calcview_eid(v["view_id"])
                row["core.assignable"]          = "True"
                row[f"{CUSTOM_PKG}.scriptType"] = node["script_type"]
                rows.append(row)
    return rows


def _extract_cte_body(sql: str, cte_name: str) -> str:
    pattern = re.compile(
        r'(?:WITH|,)\s+' + re.escape(cte_name) + r'\s+AS\s*\((.+?)(?=(?:,\s*[A-Za-z_]\w*\s+AS\s*\()|SELECT\s*\*|$)',
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(sql)
    return (m.group(1).strip() if m else "")[:2000]




# ===========================================================================
# 6. Build links.csv rows
# ===========================================================================

def build_links(parsed_views: list) -> list:
    """
    Object-level lineage (DataSetDataFlow):
      source_table -> ScriptBlock[CTE1] -> ScriptBlock[CTE2] -> HanaCalcView

    Column-level lineage (DirectionalDataFlow):
      source_col -> HanaCalcViewField   (direct, no intermediate node)

    Structural hierarchy:
      $RESOURCE -> REF_HANA_<SCHEMA>_CONN (Resource, per schema)
                     -> REF_HANA_<SCHEMA>_DS (DataSource)
                          -> REF_HANA_<SCHEMA>_DS/<TABLE> (DataSet)
                               -> REF_HANA_<SCHEMA>_DS/<TABLE>/<COL> (DataElement)
      $RESOURCE -> hana_calcviews_container (DataSource, synthetic)
                     -> HanaCalcView
                          -> HanaCalcViewField  (HanaCalcViewToHanaCalcViewField)
                          -> HanaScriptBlock    (HanaCalcViewToHanaScriptBlock)
    """
    links: list = []
    seen: set   = set()

    def add(src: str, tgt: str, assoc: str) -> None:
        key = (src, tgt, assoc)
        if key not in seen and src and tgt:
            seen.add(key)
            links.append({"Source": src, "Target": tgt, "Association": assoc})

    # $RESOURCE -> calc-view container
    add("$RESOURCE", CALCVIEW_CONTAINER_EID, ASSOC_RESOURCE_PARENT_CHILD)

    for v in parsed_views:
        view_id  = v["view_id"]
        view_eid = _custom_calcview_eid(view_id)

        # --- Source physical hierarchy: one Resource+DataSource per schema ----
        for ds in v["data_sources"]:
            schema, table = ds["schema"], ds["table"]
            if not schema or not table:
                continue
            res_eid = _schema_resource_eid(schema)
            src_eid = _schema_datasource_eid(schema)
            tbl_eid = f"{src_eid}/{table}"

            add("$RESOURCE", res_eid, ASSOC_RESOURCE_PARENT_CHILD)
            add(res_eid,     src_eid, ASSOC_RESOURCE_PARENT_CHILD)
            add(src_eid,     tbl_eid, ASSOC_DATASOURCE_PARENT_CHILD)
            for col in sorted(v.get("table_columns", {}).get((schema, table), set())):
                add(tbl_eid, f"{src_eid}/{table}/{col}", ASSOC_DATASET_ELEMENT_PARENTSHIP)

        # --- Custom object hierarchy ------------------------------------------
        add(CALCVIEW_CONTAINER_EID, view_eid, ASSOC_DATASOURCE_PARENT_CHILD)

        for field in v["output_fields"]:
            add(view_eid,
                _custom_calcviewfield_eid(view_id, field["id"]),
                ASSOC_CALCVIEW_TO_FIELD)

        for node in v["nodes"]:
            cte_names = node.get("cte_names", []) if node["script_type"] == "SCRIPT_BASED" else []
            blocks    = cte_names if cte_names else [node["node_id"]]
            for b in blocks:
                add(view_eid, _custom_scriptblock_eid(view_id, b), ASSOC_CALCVIEW_TO_SCRIPTBLOCK)

        # --- Object-level lineage (DataSetDataFlow) ---------------------------
        # Scoped: each CTE only receives edges from its own direct table deps
        # and prior CTE deps, as determined by per-CTE body analysis.
        cte_dep_map = v.get("cte_dep_map", {})
        for node in v["nodes"]:
            if node["script_type"] == "SCRIPT_BASED":
                cte_names = node.get("cte_names", [])
                if cte_names:
                    last_eid = _custom_scriptblock_eid(view_id, cte_names[-1])
                    for cte_name in cte_names:
                        cte_eid = _custom_scriptblock_eid(view_id, cte_name)
                        deps    = cte_dep_map.get(cte_name, {})

                        # Physical table upstreams scoped to this CTE
                        for (schema, table) in deps.get("tables", []):
                            add(f"{_schema_datasource_eid(schema)}/{table}",
                                cte_eid, ASSOC_DATASET_DATAFLOW)

                        # Prior CTE upstreams scoped to this CTE
                        for prior_cte in deps.get("prior_ctes", []):
                            add(_custom_scriptblock_eid(view_id, prior_cte),
                                cte_eid, ASSOC_DATASET_DATAFLOW)

                        # If no deps resolved, fall back to linking all sources to first CTE
                        if cte_name == cte_names[0] and not deps.get("tables") and not deps.get("prior_ctes"):
                            for ds in v["data_sources"]:
                                if ds["schema"] and ds["table"]:
                                    add(f"{_schema_datasource_eid(ds['schema'])}/{ds['table']}",
                                        cte_eid, ASSOC_DATASET_DATAFLOW)

                    add(last_eid, view_eid, ASSOC_DATASET_DATAFLOW)
                else:
                    block_eid = _custom_scriptblock_eid(view_id, node["node_id"])
                    for ds in v["data_sources"]:
                        if ds["schema"] and ds["table"]:
                            add(f"{_schema_datasource_eid(ds['schema'])}/{ds['table']}",
                                block_eid, ASSOC_DATASET_DATAFLOW)
                    add(block_eid, view_eid, ASSOC_DATASET_DATAFLOW)
            else:
                block_eid = _custom_scriptblock_eid(view_id, node["node_id"])
                for ds_id in node["table_inputs"]:
                    ds_match = next((d for d in v["data_sources"] if d["id"] == ds_id), None)
                    if ds_match:
                        add(f"{_schema_datasource_eid(ds_match['schema'])}/{ds_match['table']}",
                            block_eid, ASSOC_DATASET_DATAFLOW)
                add(block_eid, view_eid, ASSOC_DATASET_DATAFLOW)

        # --- Column-level lineage (DirectionalDataFlow) -----------------------
        # source_col -> HanaCalcViewField  (direct)
        table_columns = v.get("table_columns", {})
        col_to_tables: dict = {}
        for (schema, table), cols in table_columns.items():
            for col in cols:
                col_to_tables.setdefault(col.upper(), []).append((schema, table))

        for field in v["output_fields"]:
            fid            = field["id"]
            view_field_eid = _custom_calcviewfield_eid(view_id, fid)
            for schema, table in col_to_tables.get(fid.upper(), []):
                add(f"{_schema_datasource_eid(schema)}/{table}/{fid}",
                    view_field_eid, ASSOC_DIRECTIONAL_DATAFLOW)
                for actual_col in table_columns.get((schema, table), set()):
                    if actual_col.upper() == fid.upper() and actual_col != fid:
                        add(f"{_schema_datasource_eid(schema)}/{table}/{actual_col}",
                            view_field_eid, ASSOC_DIRECTIONAL_DATAFLOW)

    return links


# ===========================================================================
# 7. CSV serialiser and ZIP writer
# ===========================================================================

def _to_csv_bytes(rows: list, fields: list) -> bytes:
    buf = io.StringIO()
    w   = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore",
                         quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


# ===========================================================================
# 8. Orchestration
# ===========================================================================

def process(source: str, output_zip: str) -> None:
    script_files = find_scripts(source)
    print(f"Found {len(script_files)} calculation view file(s):\n")

    parsed_views = []
    for fpath in script_files:
        parsed = parse_calcview(fpath)
        cte_count = sum(len(n.get("cte_names", [])) for n in parsed["nodes"])
        print(f"  {Path(fpath).name}")
        print(f"    view_id={parsed['view_id']}  "
              f"sources={len(parsed['data_sources'])}  "
              f"nodes={len(parsed['nodes'])}  "
              f"ctes={cte_count}  "
              f"output_fields={len(parsed['output_fields'])}")
        parsed_views.append(parsed)

    print(f"\nTotal views: {len(parsed_views)}\n")

    core_resources    = build_core_resource(parsed_views)
    core_datasources  = build_core_datasources(parsed_views)
    core_datasets     = build_core_datasets(parsed_views)
    core_dataelements = build_core_dataelements(parsed_views)
    custom_views      = build_custom_calcviews(parsed_views)
    custom_fields     = build_custom_calcview_fields(parsed_views)
    custom_blocks     = build_custom_scriptblocks(parsed_views)
    links             = build_links(parsed_views)

    Path(output_zip).parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(name: str, rows: list, fields: list) -> None:
            zf.writestr(name, _to_csv_bytes(rows, fields))
            print(f"  {name:<70}  {len(rows):>5} row(s)")

        print("--- Core reference CSVs ---")
        add("core.Resource.csv",    core_resources,    CORE_RESOURCE_FIELDS)
        add("core.DataSource.csv",  core_datasources,  CORE_DATASOURCE_FIELDS)
        add("core.DataSet.csv",     core_datasets,     CORE_DATASET_FIELDS)
        add("core.DataElement.csv", core_dataelements, CORE_DATAELEMENT_FIELDS)

        print("\n--- Custom metamodel CSVs ---")
        add(f"{CUSTOM_PKG}.HanaCalcView.csv",      custom_views,  CUSTOM_CALCVIEW_FIELDS)
        add(f"{CUSTOM_PKG}.HanaCalcViewField.csv", custom_fields, CUSTOM_CALCVIEWFIELD_FIELDS)
        add(f"{CUSTOM_PKG}.HanaScriptBlock.csv",   custom_blocks, CUSTOM_SCRIPTBLOCK_FIELDS)

        print("\n--- links.csv ---")
        add("links.csv", links, LINKS_FIELDS)

    print(f"\nOutput: {output_zip}")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else SCRIPTS_DIR
    try:
        process(source, OUTPUT_ZIP)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
