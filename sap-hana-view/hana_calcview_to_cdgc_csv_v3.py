"""
hana_calcview_to_cdgc_csv_v3.py
================================
Parses SAP HANA Calculation View XML files (.hdbcalculationview) and generates
a ZIP matching the Informatica CDGC custom scanner import format for:
  custom.sap.hana.calcscript.v3  (SAPHANACalcScript_v3.json)

v3 changes vs v2:
  - packageName corrected to custom.sap.hana.calcscript.v2
  - HanaCalcView and HanaTable now extend core.DataSource (self-rooted), so
    no ParentChild lookup is needed against HanaPackage at ingest time.
    The framework processes CSVs alphabetically; making every top-level class
    self-rooted avoids the "parent not published yet" error regardless of
    processing order.
  - HanaPackageToHanaCalcView and HanaPackageToHanaTable downgraded from
    ParentChild to RelatedKind (grouping links, not hierarchy).

Class CSVs produced:
  custom.sap.hana.calcscript.v3.HanaPackage.csv
  custom.sap.hana.calcscript.v3.HanaTable.csv
  custom.sap.hana.calcscript.v3.HanaCalcView.csv
  custom.sap.hana.calcscript.v3.HanaCalcViewField.csv
  custom.sap.hana.calcscript.v3.HanaScriptBlock.csv

Relationships (single links.csv):
  Source, Target, Association

Usage:
  python hana_calcview_to_cdgc_csv_v3.py                        # all *.hdbcalculationview in ./sample_scripts/
  python hana_calcview_to_cdgc_csv_v3.py path/to/scripts/dir    # specific directory
  python hana_calcview_to_cdgc_csv_v3.py path/to/single.hdbcalculationview
  HANA_SCRIPTS_DIR=./my_scripts  python hana_calcview_to_cdgc_csv_v3.py
"""

import csv
import glob
import io
import os
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

import json

def load_cdgc_model(json_path: str):
    """Load PKG, ASSOC, and CLASS_FIELDS dynamically from the CDGC JSON model file."""
    with open(json_path, 'r', encoding='utf-8') as f:
        model = json.load(f)
        
    pkg = model.get("packageName", "custom.sap.hana.calcscript.v3")
    
    assoc_map = {}
    for assoc in model.get("associations", []):
        name = assoc["name"]
        if name == "HanaPackageToHanaCalcView": assoc_map["PackageToCalcView"] = f"{pkg}.{name}"
        elif name == "HanaPackageToHanaTable": assoc_map["PackageToTable"] = f"{pkg}.{name}"
        elif name == "HanaTableToHanaTableField": assoc_map["TableToField"] = f"{pkg}.{name}"
        elif name == "HanaTableToHanaScriptBlock": assoc_map["TableToScriptBlock"] = f"{pkg}.{name}"
        elif name == "HanaCalcViewToHanaCalcViewField": assoc_map["CalcViewToField"] = f"{pkg}.{name}"
        elif name == "HanaCalcViewToHanaScriptBlock": assoc_map["CalcViewToScriptBlock"] = f"{pkg}.{name}"
        elif name == "HanaScriptBlockToHanaCalcView": assoc_map["ScriptBlockToCalcView"] = f"{pkg}.{name}"

    class_fields = {}
    for cls in model.get("classes", []):
        cname = cls["name"]
        super_classes = cls.get("superClasses", [])
        
        # Default core fields
        fields = ["core.externalId", "core.name", "core.description", "core.reference"]
        
        # Add core.assignable for DataSources and DataSets
        if any(sc in ["core.DataSource", "core.DataSet"] for sc in super_classes):
            fields.append("core.assignable")
            
        if cname == "HanaTableField":
            fields = ["core.externalId", "core.name", "core.reference"] # minimal for TableField
            
        class_fields[cname] = fields

    for cattr in model.get("classAttributes", []):
        cname = cattr["className"].split(".")[-1]
        if cname in class_fields:
            class_fields[cname].append(cattr["attributeName"])
            
    return pkg, assoc_map, class_fields

SCRIPTS_DIR   = os.getenv("HANA_SCRIPTS_DIR", str(Path(__file__).parent / "input"))
OUTPUT_ZIP    = os.getenv("CSV_OUTPUT_ZIP",   str(Path(__file__).parent / "output" / "hana_cdgc_import_v3.zip"))
MODEL_JSON    = os.getenv("CDGC_MODEL_JSON",  str(Path(__file__).parent / "SAPHANACalcScript_v3.json"))
RESOURCE_NAME = os.getenv("CDGC_RESOURCE_NAME", "Test_SOCAR_CalculatioView")

# These will be initialized in process() based on the JSON file
PKG = ""
ASSOC = {}
CLASS_FIELDS = {}

LINKS_FIELDS = ["Source", "Target", "Association"]

# XML namespace used in .hdbcalculationview files
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


def _calc_expressions(node_elem) -> list:
    """Return list of (attribute_id, expression) from calculatedViewAttributes."""
    results = []
    for cva in node_elem.findall(".//calculatedViewAttribute"):
        attr_id = cva.get("id", "")
        km = cva.find("keyMapping")
        expr = km.get("columnName", "") if km is not None else ""
        if attr_id:
            results.append((attr_id, expr))
    return results


def _direct_table_inputs(node_elem, ds_ids: set) -> list:
    """Return input node IDs that reference actual DataSources (not sibling nodes)."""
    return [
        i.get("node", "").lstrip("#")
        for i in node_elem.findall("input")
        if i.get("node", "").lstrip("#") in ds_ids
    ]


def parse_calcview(path: str) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()

    view_id    = root.get("id", Path(path).stem)
    package    = root.get("package", "")
    calc_type  = root.get("dataCategory", "CUBE")
    def_client = root.get("defaultClient", "")

    desc_elem   = root.find("descriptions")
    description = desc_elem.get("defaultDescription", "") if desc_elem is not None else ""

    data_sources = []
    for ds in root.findall(".//DataSource"):
        ds_id = ds.get("id", "")
        res   = ds.find("resourceUri")
        uri   = res.text.strip() if (res is not None and res.text) else ""
        schema, _, table = uri.partition("/")
        data_sources.append({"id": ds_id, "schema": schema, "table": table})

    ds_ids = {ds["id"] for ds in data_sources}

    nodes = []
    for node_elem in root.findall(".//calculationView"):
        xsi_type    = node_elem.get(f"{{{XSI_NS}}}type", "")
        node_id     = node_elem.get("id", "")
        stype       = _node_script_type(xsi_type)
        jt_raw      = node_elem.get("joinType", "")
        join_type   = JOIN_TYPE_MAP.get(jt_raw.lower(), "") if stype == "JOIN" else ""
        filter_elem = node_elem.find("filter")
        filter_cond = (filter_elem.text or "").strip() if filter_elem is not None else ""
        calc_exprs  = _calc_expressions(node_elem)
        table_inputs = _direct_table_inputs(node_elem, ds_ids)

        nodes.append({
            "node_id":          node_id,
            "script_type":      stype,
            "join_type":        join_type,
            "filter_condition": filter_cond,
            "calc_expressions": calc_exprs,
            "table_inputs":     table_inputs,
        })

    output_fields = []
    lm = root.find("logicalModel")
    if lm is not None:
        for attr in lm.findall(".//attribute"):
            output_fields.append({"id": attr.get("id", ""), "key_attribute": "true"})
        for measure in lm.findall(".//measure"):
            output_fields.append({"id": measure.get("id", ""), "key_attribute": "false"})

    return {
        "view_id":        view_id,
        "package":        package,
        "description":    description,
        "calc_view_type": calc_type,
        "default_client": def_client,
        "data_sources":   data_sources,
        "nodes":          nodes,
        "output_fields":  output_fields,
    }


# ===========================================================================
# 3. External ID helpers
# ===========================================================================

def ext_id(class_short: str, *parts: str) -> str:
    """Generate core.externalId in the format Package.ClassName/Part1/Part2"""
    prefix = f"{PKG}.{class_short}" if PKG else class_short
    return f"{prefix}/" + "/".join(p for p in parts if p)


# ===========================================================================
# 4. Build class rows
# ===========================================================================

def empty_row(fields: list) -> dict:
    return {f: "" for f in fields}


def build_hana_packages(parsed_views: list) -> list:
    """One HanaPackage (core.DataSource, self-rooted) per unique schema."""
    rows = []
    seen = set()
    for v in parsed_views:
        for ds in v["data_sources"]:
            schema = ds["schema"]
            if schema and schema not in seen:
                seen.add(schema)
                row = empty_row(CLASS_FIELDS.get("HanaPackage", []))
                row["core.externalId"]  = ext_id("HanaPackage", schema)
                row["core.name"]        = schema
                row["core.description"] = f"SAP HANA schema / package: {schema}"
                row["core.reference"]   = "true"
                row["core.assignable"]  = "true"
                rows.append(row)
    return rows


def build_hana_tables(parsed_views: list) -> tuple:
    """
    Build HanaTable rows (core.DataSource, self-rooted — no parent lookup at ingest).
    Returns (table_rows, field_rows).
    """
    table_rows, field_rows = [], []
    seen_tables = set()

    for v in parsed_views:
        for ds in v["data_sources"]:
            key = (ds["schema"], ds["table"])
            if key in seen_tables:
                continue
            seen_tables.add(key)

            t_row = empty_row(CLASS_FIELDS.get("HanaTable", []))
            t_row["core.externalId"]    = ext_id("HanaTable", ds["schema"], ds["table"])
            t_row["core.name"]          = ds["table"]
            t_row["core.description"]   = f"SAP HANA table {ds['schema']}.{ds['table']}"
            t_row["core.reference"]     = "true"
            t_row["core.assignable"]    = "true"
            t_row[f"{PKG}.tableSchema"] = ds["schema"]
            table_rows.append(t_row)

    return table_rows, field_rows


def build_calc_views(parsed_views: list) -> list:
    """HanaCalcView rows (core.DataSource, self-rooted — no parent lookup at ingest)."""
    rows = []
    for v in parsed_views:
        row = empty_row(CLASS_FIELDS.get("HanaCalcView", []))
        row["core.externalId"]      = ext_id("HanaCalcView", v["view_id"])
        row["core.name"]            = v["view_id"]
        row["core.description"]     = v["description"]
        row["core.reference"]       = "true"
        row["core.assignable"]      = "true"
        row[f"{PKG}.packagePath"]   = v["package"]
        row[f"{PKG}.sourceSchema"]  = v["data_sources"][0]["schema"] if v["data_sources"] else ""
        row[f"{PKG}.calcViewType"]  = v["calc_view_type"]
        row[f"{PKG}.defaultClient"] = v["default_client"]
        rows.append(row)
    return rows


def build_calc_view_fields(parsed_views: list) -> list:
    rows = []
    for v in parsed_views:
        parent_eid = ext_id("HanaCalcView", v["view_id"])
        for field in v["output_fields"]:
            row = empty_row(CLASS_FIELDS.get("HanaCalcViewField", []))
            row["core.externalId"]     = ext_id("HanaCalcViewField", v["view_id"], field['id'])
            row["core.name"]           = field["id"]
            if "core.description" in row:
                row["core.description"] = f"Field {field['id']} for view {v['view_id']}"
            row["core.reference"]      = "true"
            row[f"{PKG}.keyAttribute"] = field["key_attribute"]
            rows.append(row)
    return rows


def build_script_blocks(parsed_views: list) -> list:
    rows = []
    for v in parsed_views:
        parent_eid = ext_id("HanaCalcView", v["view_id"])
        for node in v["nodes"]:
            logic_parts = [f"{col} = {expr}" for col, expr in node["calc_expressions"]]
            logic = "; ".join(logic_parts)

            score = (
                len(node["calc_expressions"])
                + (1 if node["join_type"] else 0)
                + (1 if node["filter_condition"] else 0)
            )

            row = empty_row(CLASS_FIELDS.get("HanaScriptBlock", []))
            row["core.externalId"]            = ext_id("HanaScriptBlock", v["view_id"], node['node_id'])
            row["core.name"]                  = node["node_id"]
            row["core.description"]           = f"{node['script_type']} node in {v['view_id']}"
            row["core.reference"]             = "true"
            row["core.assignable"]            = "true"
            row[f"{PKG}.scriptType"]          = node["script_type"]
            row[f"{PKG}.transformationLogic"] = logic
            row[f"{PKG}.joinType"]            = node["join_type"]
            row[f"{PKG}.filterCondition"]     = node["filter_condition"]
            row[f"{PKG}.complexityScore"]     = str(score)
            row[f"{PKG}.sourceTableCount"]    = str(len(node["table_inputs"]))
            row[f"{PKG}.sourceSchema"]        = v["data_sources"][0]["schema"] if v["data_sources"] else ""
            rows.append(row)
    return rows


# ===========================================================================
# 5. Build links.csv rows
# ===========================================================================

def build_links(parsed_views: list) -> list:
    links = []
    seen  = set()

    def add(src: str, tgt: str, assoc: str) -> None:
        key = (src, tgt, assoc)
        if key not in seen and src and tgt:
            seen.add(key)
            links.append({"Source": src, "Target": tgt, "Association": assoc})

    for v in parsed_views:
        view_eid   = ext_id("HanaCalcView", v["view_id"])
        ds_map     = {ds["id"]: ds for ds in v["data_sources"]}
        pkg_schema = v["data_sources"][0]["schema"] if v["data_sources"] else ""

        # Link root object to Resource
        add(RESOURCE_NAME, view_eid, "core.ResourceParentChild")

        if pkg_schema:
            package_eid = ext_id("HanaPackage", pkg_schema)
            # Link root object to Resource
            add(RESOURCE_NAME, package_eid, "core.ResourceParentChild")
            # RelatedKind grouping links (not ParentChild — both ends are self-rooted)
            add(package_eid, view_eid, ASSOC.get("PackageToCalcView", ""))

        # HanaPackage -> HanaTable  (RelatedKind grouping)
        for ds in v["data_sources"]:
            if ds["schema"]:
                table_eid = ext_id("HanaTable", ds["schema"], ds["table"])
                # Link root object to Resource
                add(RESOURCE_NAME, table_eid, "core.ResourceParentChild")
                
                add(ext_id("HanaPackage", ds["schema"]),
                    table_eid,
                    ASSOC.get("PackageToTable", ""))

        # HanaCalcView -> HanaCalcViewField  (ParentChild — CalcView is already ingested)
        for field in v["output_fields"]:
            field_eid = ext_id("HanaCalcViewField", v["view_id"], field['id'])
            add(view_eid, field_eid, ASSOC.get("CalcViewToField", ""))

        for node in v["nodes"]:
            block_eid = ext_id("HanaScriptBlock", v["view_id"], node['node_id'])

            # HanaCalcView -> HanaScriptBlock  (ParentChild — CalcView already ingested)
            add(view_eid, block_eid, ASSOC.get("CalcViewToScriptBlock", ""))

            # HanaScriptBlock -> HanaCalcView  (dataflow, RelatedKind)
            add(block_eid, view_eid, ASSOC.get("ScriptBlockToCalcView", ""))

            # HanaTable -> HanaScriptBlock  (dataflow, RelatedKind)
            for ds_id in node["table_inputs"]:
                if ds_id in ds_map:
                    ds = ds_map[ds_id]
                    src_eid = ext_id("HanaTable", ds["schema"], ds["table"])
                    add(src_eid, block_eid, ASSOC.get("TableToScriptBlock", ""))

    return links


# ===========================================================================
# 6. CSV serialiser and ZIP writer
# ===========================================================================

def to_csv_bytes(rows: list, fields: list) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore",
                            quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def to_links_csv_bytes(rows: list) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=LINKS_FIELDS, extrasaction="ignore",
                            quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# ===========================================================================
# 7. Orchestration
# ===========================================================================

def process(source: str, output_zip: str, model_json: str) -> None:
    global PKG, ASSOC, CLASS_FIELDS
    
    print(f"Loading CDGC Model from {model_json}...")
    PKG, ASSOC, CLASS_FIELDS = load_cdgc_model(model_json)
    
    script_files = find_scripts(source)
    print(f"Found {len(script_files)} calculation view file(s):\n")

    parsed_views = []
    seen_views = set()
    for fpath in script_files:
        parsed = parse_calcview(fpath)
        view_id = parsed['view_id']
        
        if view_id in seen_views:
            print(f"  Skipping duplicate view_id '{view_id}' from {Path(fpath).name}")
            continue
            
        seen_views.add(view_id)
        print(f"  {Path(fpath).name}")
        print(f"    view_id={view_id}  "
              f"sources={len(parsed['data_sources'])}  "
              f"nodes={len(parsed['nodes'])}  "
              f"output_fields={len(parsed['output_fields'])}")
        parsed_views.append(parsed)

    print(f"\nTotal unique views: {len(parsed_views)}\n")

    hana_packages               = build_hana_packages(parsed_views)
    hana_tables, _              = build_hana_tables(parsed_views)
    calc_views    = build_calc_views(parsed_views)
    view_fields   = build_calc_view_fields(parsed_views)
    script_blocks = build_script_blocks(parsed_views)
    links         = build_links(parsed_views)

    Path(output_zip).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        def write_csv(name, rows, fields, count):
            zf.writestr(name, to_csv_bytes(rows, fields))
            print(f"  {name:<70}  {count:>5} row(s)")

        print("--- Class CSVs ---")

        # core.Resource.csv — catalog source entry
        resource_rows = [{"core.externalId": RESOURCE_NAME, "core.reference": "false", "core.name": RESOURCE_NAME}]
        resource_fields = ["core.externalId", "core.reference", "core.name"]
        write_csv("core.Resource.csv", resource_rows, resource_fields, len(resource_rows))

        write_csv(f"{PKG}.HanaPackage.csv",       hana_packages, CLASS_FIELDS["HanaPackage"],         len(hana_packages))
        write_csv(f"{PKG}.HanaTable.csv",         hana_tables,   CLASS_FIELDS["HanaTable"],           len(hana_tables))
        write_csv(f"{PKG}.HanaCalcView.csv",      calc_views,    CLASS_FIELDS["HanaCalcView"],        len(calc_views))
        write_csv(f"{PKG}.HanaCalcViewField.csv", view_fields,   CLASS_FIELDS["HanaCalcViewField"],   len(view_fields))
        write_csv(f"{PKG}.HanaScriptBlock.csv",   script_blocks, CLASS_FIELDS["HanaScriptBlock"],     len(script_blocks))

        print("\n--- links.csv ---")
        zf.writestr("links.csv", to_links_csv_bytes(links))
        print(f"  {'links.csv':<70}  {len(links):>5} row(s)")

    print(f"\nOutput: {output_zip}")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else SCRIPTS_DIR
    model_json = sys.argv[2] if len(sys.argv) > 2 else MODEL_JSON
    try:
        process(source, OUTPUT_ZIP, model_json)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
