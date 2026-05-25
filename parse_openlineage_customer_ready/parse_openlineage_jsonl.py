#!/usr/bin/env python3
"""
OpenLineage JSONL -> core.* CSVs + links.csv (exact format)

Outputs:
1) core.Resource.csv
   columns: core.externalId, core.Reference, core.assignable, core.name
   - Two rows: Source resource & Target resource (IDs and names from CLI)

2) core.DataSource.csv
   columns: core.externalId, core.Reference, core.assignable, core.name
   - One row per (resource, database/schema) encountered in inputs/outputs
   - Default externalId = "<resourceId>.<db>"
   - Default name       = "<resourceId>.<db>"
   - Can be overridden via --ds-map CSV:
       columns: resourceId, db, core.externalId, core.name
       (case-insensitive match on resourceId & db; name is optional)

3) core.DataSet.csv
   columns: core.externalId, core.Reference, core.assignable, core.name
   - One row per table: extId = "<resourceId>.<db>/<table>", name = "<table>"

4) core.DataElement.csv
   columns: core.externalId, core.Reference, core.assignable, core.name
   - One row per column: extId = "<resourceId>.<db>/<table>/<column>", name = "<column>"

5) com.infa.odin.models.relational.Calculation.csv
   columns: core.externalId, core.Reference, core.assignable, core.name
   - One row per output column present in the OpenLineage columnLineage facet

6) links.csv (3 columns): Source, Target, Association
   - core.ResourceParentChild:
       $resource(source) -> reference resource(source)
       $resource(target) -> reference resource(target)
       reference resource -> each DataSource
   - core.DataSourceParentChild: DataSource -> DataSet
   - core.DataSetToDataElementParentship: DataSet -> DataElement
   - core.DataSetDataFlow: input DataSet -> output DataSet (per run)
   - com.infa.odin.models.relational.StatementToCalculation: Statement -> Calculation
   - core.DirectionalDataFlow: input DataElement -> Calculation -> output DataElement
     (if columnLineage facet exists)

Usage:
  python parse_openlineage_jsonl.py \
      --in ./input \
      --out ./output

  # Or process one file:
  python parse_openlineage_jsonl.py \
      --in ./input/events.jsonl \
      --out ./output

The script is tolerant of dataset names:
- "catalog.schema.table" -> datasource="catalog.schema", table="table"
- "schema.table"         -> datasource="schema", table="table"
- path-like "/mnt/foo/bar" -> datasource="/mnt/foo", table="bar"
"""
import argparse
import csv
import json
import os
import itertools
import zipfile
from pathlib import Path
from parser_utils import write_reference_assets_to_csv
from typing import Dict, Any, List, Tuple, Optional, Iterable

import pandas as pd


# ---------- helpers ----------
def normalize_path(p: str) -> str:
    if p.startswith("dbfs:/"):
        return p.replace("dbfs:/", "/dbfs/", 1)
    return p


def strip_quotes(x: Optional[str]) -> Optional[str]:
    if not isinstance(x, str):
        return x
    return x.strip("`").strip('"').strip("'")


def split_dataset_name(name: str) -> Tuple[str, str]:
    """
    Return (db, table) as strings.
    - If 'catalog.schema.table' -> (schema, table)
    - If 'schema.table' -> (schema, table)
    - If '/path/like/name' -> (parent, name)
    - Else -> ('_default', name)
    """
    name = (name or "").strip()
    if not name:
        return ("_default", "_unknown")
    # path-like without dots
    if "/" in name and "." not in name:
        parts = [p for p in name.split("/") if p]
        table = parts[-1]
        db = parts[-2] if len(parts) >= 2 else "_path"
        return (db, table)
    # dotted
    dot_parts = [strip_quotes(p) for p in name.split(".")]
    if len(dot_parts) >= 3:
        # catalog.schema.table -> use schema, table
        return (dot_parts[-2], dot_parts[-1])
    if len(dot_parts) == 2:
        return (dot_parts[0], dot_parts[1])
    return ("_default", dot_parts[0])


def harvest_columns(ds: Dict[str, Any]) -> List[str]:
    """Return list of column names if schema facet exists."""
    facets = ds.get("facets") or {}
    schema = (facets.get("schema") or {}).get("fields") or []
    cols = []
    for f in schema:
        n = f.get("name")
        if n:
            cols.append(strip_quotes(n))
    return cols


def unique(seq: Iterable) -> List:
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


OUTPUT_FILES = [
    'com.infa.odin.models.relational.Database.csv',
    'com.infa.odin.models.relational.Schema.csv',
    'com.infa.odin.models.relational.Task.csv',
    'core.Resource.csv',
    'core.DataSource.csv',
    'core.DataSet.csv',
    'core.DataElement.csv',
    'com.infa.odin.models.relational.Statement.csv',
    'com.infa.odin.models.relational.Calculation.csv',
    'links.csv',
    'cdgc_openlineage_output.zip',
]

CSV_OUTPUT_FILES = [
    file_name for file_name in OUTPUT_FILES if file_name.endswith(".csv")
]


def discover_input_files(in_path: str) -> List[str]:
    """Return JSONL/NDJSON input files from a file path or directory."""
    path = Path(normalize_path(in_path))
    if path.is_file():
        return [str(path)]
    if not path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {in_path}")

    supported_extensions = {".jsonl", ".ndjson"}
    files = [
        str(p)
        for p in sorted(path.iterdir())
        if p.is_file() and p.suffix.lower() in supported_extensions
    ]
    if not files:
        raise FileNotFoundError(f"No .jsonl or .ndjson files found in: {in_path}")
    return files


def clear_generated_outputs(out_dir: str) -> None:
    """Remove CSVs this script generates so each run rewrites output cleanly."""
    for file_name in OUTPUT_FILES:
        output_path = os.path.join(out_dir, file_name)
        if os.path.isfile(output_path):
            os.remove(output_path)


def create_output_zip(out_dir: str, zip_name: str = "cdgc_openlineage_output.zip") -> str:
    """Create a zip package containing generated CSV files."""
    zip_path = os.path.join(out_dir, zip_name)
    if os.path.isfile(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_name in CSV_OUTPUT_FILES:
            csv_path = os.path.join(out_dir, file_name)
            if os.path.isfile(csv_path):
                zipf.write(csv_path, arcname=file_name)
    return zip_path


def read_events(input_files: List[str]) -> List[Dict[str, Any]]:
    """Read OpenLineage events from all JSONL/NDJSON files."""
    events: List[Dict[str, Any]] = []
    for input_file in input_files:
        with open(input_file, "r", encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"Skipping invalid JSON in {input_file}:{line_number}")
    return events


# ---------- mapping ----------
def load_ds_map(path: Optional[str]) -> List[Dict[str, str]]:
    """
    Mapping CSV columns (case-insensitive):
      resourceId, db, core.externalId, core.name
    - resourceId optional: if omitted, mapping applies regardless of resource
    - core.name optional: if omitted, name=externalId
    """
    if not path:
        return []
    path = normalize_path(path)
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rr = {k.lower(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            rows.append(rr)
    return rows


def apply_ds_override(resource_id: str, db: str, default_ext: str, default_name: str, mapping: List[Dict[str, str]]) -> Tuple[str, str]:
    """Return (extId, name) possibly overridden by mapping."""
    db_l = (db or "").lower()
    rid_l = (resource_id or "").lower()
    for m in mapping:
        m_db = (m.get("db") or "").lower()
        if not m_db or m_db != db_l:
            continue
        m_rid = (m.get("resourceid") or "").lower()
        if m_rid and m_rid != rid_l:
            continue
        ext = m.get("core.externalid") or m.get("externalid")
        nm  = m.get("core.name") or m.get("name")
        if ext:
            return (ext, nm or ext)
    return (default_ext, default_name)


def escape_resource_name(rsc_name):
    rsc_name = rsc_name or ""
    return rsc_name.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(".", "_").replace("-", "_").replace(":", "_")

def convert_to_datasource_table(data_src_name):
    data_src_name = data_src_name or "_unknown"
    ds = "_default"
    table = data_src_name
    delimiters = ['.', '/']
    for delim in delimiters:
        if len(data_src_name.rsplit(delim, 1)) == 2:
            ds,table = data_src_name.rsplit(delim, 1)
    return ds, table

# Unique datasets
def datasource_key(d, default_src='dft_src'):
    # can be sql server endpoint host:port etc.
    # can also be a string like dbfs
    rsc_name = escape_resource_name(d.get("namespace")) or default_src
    # will be db.schema.table format or
    # /user/hive/warehouse/tablename
    data_src_name, table_name = convert_to_datasource_table(d.get("name"))
    return f"{rsc_name}.{data_src_name}/{table_name}"

def event_to_dskey_col_map(ev):
    m = {}
    for l in ("inputs", "outputs"):
        for d in ev.get(l) or []:
            m[datasource_key(d)] = flatten_schema_fields(d.get('facets', {}).get('schema', {}).get('fields', []))
    return m


def normalize_field_name(field_name):
    if comparable_column_name(field_name) == "timestamp":
        return "timestamp"
    return field_name


def flatten_schema_fields(fields):
    """Return leaf field names from a possibly nested schema."""
    flattened = []
    for field in fields or []:
        field_name = strip_quotes(field.get("name"))
        if not field_name:
            continue
        nested_fields = field.get("fields") or []
        if nested_fields:
            flattened.extend(flatten_schema_fields(nested_fields))
        else:
            flattened.append({"name": normalize_field_name(field_name)})
    return flattened


def nested_field_lookup(fields):
    """Map top-level fields to their flattened leaf names."""
    lookup = {}
    for field in fields or []:
        field_name = strip_quotes(field.get("name"))
        if not field_name:
            continue
        flattened = flatten_schema_fields([field])
        lookup[field_name] = [f["name"] for f in flattened] or [field_name]
    return lookup


def comparable_column_name(name):
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def is_column_match(target_column, source_column):
    target = comparable_column_name(target_column)
    source_leaf = comparable_column_name((source_column or "").split(".")[-1])
    if target == source_leaf:
        return True
    if target.endswith("datetime") and source_leaf.endswith("timestamp"):
        return True
    return False


def has_direct_transformation(input_field):
    transformations = input_field.get("transformations") or []
    return any((t.get("type") or "").upper() == "DIRECT" for t in transformations)


def resolve_source_columns(input_field, target_column, dataset_field_lookup):
    """Resolve OpenLineage container fields to nested leaf fields where possible."""
    source_field = strip_quotes(input_field.get("field"))
    if not source_field:
        return []

    candidate_fields = dataset_field_lookup.get(source_field, [source_field])
    matching_fields = [field for field in candidate_fields if is_column_match(target_column, field)]
    if matching_fields:
        return matching_fields

    if len(candidate_fields) == 1 and candidate_fields[0] == source_field:
        return candidate_fields

    if has_direct_transformation(input_field):
        return candidate_fields

    return []


def column_lineage_links(output_dataset, statement_id, dataset_field_lookups):
    """Build calculation and data element lineage sets from an output columnLineage facet."""
    dataset_set = set()
    dataelement_set = set()
    calculation_set = set()
    statement_calculation_relationship_set = set()
    data_element_lineage_set = set()

    output_facets = output_dataset.get("facets") or {}
    column_lineage = (output_facets.get("columnLineage") or {}).get("fields") or {}
    if not column_lineage:
        return (
            dataset_set,
            dataelement_set,
            calculation_set,
            statement_calculation_relationship_set,
            data_element_lineage_set,
        )

    target_dataset_id = datasource_key(output_dataset)
    dataset_set.add(target_dataset_id)

    for target_column, lineage_details in column_lineage.items():
        target_column = strip_quotes(target_column)
        if not target_column:
            continue

        target_de_id = f"{target_dataset_id}/{target_column}"
        calculation_id = f"{statement_id}/{target_column}"
        dataelement_set.add(target_de_id)
        calculation_set.add(calculation_id)
        statement_calculation_relationship_set.add((statement_id, calculation_id))
        data_element_lineage_set.add((calculation_id, target_de_id))

        input_fields = (lineage_details or {}).get("inputFields") or []
        direct_input_fields = [input_field for input_field in input_fields if has_direct_transformation(input_field)]
        lineage_input_fields = direct_input_fields or input_fields
        for input_field in lineage_input_fields:
            source_dataset = {
                "namespace": input_field.get("namespace"),
                "name": input_field.get("name"),
            }
            source_dataset_id = datasource_key(source_dataset)
            source_columns = resolve_source_columns(
                input_field,
                target_column,
                dataset_field_lookups.get(source_dataset_id, {}),
            )

            dataset_set.add(source_dataset_id)
            for source_column in source_columns:
                source_de_id = f"{source_dataset_id}/{source_column}"
                dataelement_set.add(source_de_id)
                data_element_lineage_set.add((source_de_id, calculation_id))

    return (
        dataset_set,
        dataelement_set,
        calculation_set,
        statement_calculation_relationship_set,
        data_element_lineage_set,
    )


def process_lineage_event(event):
    dataset_set = set()
    dataelement_set = set()
    data_source_set = set()
    resource_set = set()
    dataset_lineage_set = set()
    data_element_lineage_set = set()
    statement_calculation_set = set()
    statement_calculation_relationship_set = set()
    statement_set = set()

    # Extract job/run related details
    statement_app_name = event.get('run', {}).get('facets', {}).get('spark_properties', {}).get('properties', {}).get('spark.app.name', 'default_app')
    statement_job_namespace = event.get('job', {}).get('namespace', 'default_namespace')
    statement_job_name = event.get('job', {}).get('name', 'default_job')

    # Convert job/run related details to CDGC ID formats
    db_id = escape_resource_name(statement_app_name)
    schema_id = f"{db_id}/{statement_job_namespace}"
    task_id = f"{schema_id}/{statement_job_name}"

    # Gather datasets by role
    input_datasets: List[Dict[str, Any]] = []
    output_datasets: List[Dict[str, Any]] = []
    for d in event.get("inputs") or []:
        input_datasets.append(d)
    for d in event.get("outputs") or []:
        output_datasets.append(d)

    dataset_field_lookups = {
        datasource_key(d): nested_field_lookup(d.get('facets', {}).get('schema', {}).get('fields', []))
        for d in input_datasets + output_datasets
    }

    # Add input and output sources into dataset
    inputs = unique(datasource_key(d) for d in input_datasets)  # unique keys
    dataset_set.update(inputs)
    outputs = unique(datasource_key(d) for d in output_datasets)
    dataset_set.update(outputs)

    # Add column ids to master set
    ds_col_map = event_to_dskey_col_map(event)
    for ds_key, cols in ds_col_map.items():
        for col in cols:
            dataelement_set.add(f"{ds_key}/{col.get('name')}")

    # Add dataset lineage
    # But first need to create a intermediate representation of the job
    # Follow the structure of app/namespace/job/statement
    # which internally map to relational objects of db/schema/task/statement
    statement_name = event.get("job", {}).get('facets', {}).get('jobType', {}).get('jobType', 'Default_SPARK_JOB')
    statement_details = event.get("job", {}).get('facets', {}).get('sql', {}).get('query', None)
    statement_id = f"{task_id}/{statement_name}"
    for input in inputs:
        for output in outputs:
            dataset_lineage_set.add((input, statement_id))
            dataset_lineage_set.add((statement_id, output))
    statement_set.add((statement_id, "","TRUE", statement_name, statement_details))

    for output_dataset in output_datasets:
        (
            lineage_dataset_set,
            lineage_dataelement_set,
            lineage_calculation_set,
            lineage_statement_calculation_relationship_set,
            lineage_data_element_lineage_set,
        ) = column_lineage_links(output_dataset, statement_id, dataset_field_lookups)
        dataset_set.update(lineage_dataset_set)
        dataelement_set.update(lineage_dataelement_set)
        statement_calculation_set.update(lineage_calculation_set)
        statement_calculation_relationship_set.update(lineage_statement_calculation_relationship_set)
        data_element_lineage_set.update(lineage_data_element_lineage_set)

    # Extract resource, datasource, and table from ds id
    for ds in dataset_set:
        try:
            resource, rest = ds.split('.', 1)
            datasource, _ = rest.rsplit('/', 1)
        except ValueError:
            # fallback if format is unexpected
            resource, datasource, _ = ds, "_unknown", "_unknown"
        resource_set.add(resource)
        data_source_set.add(f"{resource}.{datasource}")

    return (
        dataset_set,
        dataelement_set,
        data_source_set,
        resource_set,
        dataset_lineage_set,
        data_element_lineage_set,
        statement_set,
        statement_calculation_set,
        statement_calculation_relationship_set,
        db_id,
        schema_id,
        task_id,
    )


# ---------- main conversion ----------
def convert(in_path: str,
            out_dir: str) -> None:
    in_path = normalize_path(in_path)
    out_dir = normalize_path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    clear_generated_outputs(out_dir)

    input_files = discover_input_files(in_path)
    events = read_events(input_files)

    # Partial events won't derive lineage
    # Filter out events with no inputs or outputs
    # Now each event should be an lineage tuple
    events = [e for e in events if e.get("inputs") and e.get("outputs")]

    dataset_set_master = set()
    dataelement_set_master = set()
    data_source_set_master = set()
    resource_set_master = set()
    dataset_lineage_set_master = set()
    data_element_lineage_set_master = set()
    statement_set_master = set()
    statement_calculation_set_master = set()
    statement_calculation_relationship_set_master = set()
    script_db_master = set()
    script_schema_master = set()
    script_tasks_master = set()

    dataset_rows = []
    dataelement_rows = []
    data_source_rows = []
    resource_rows = []
    statement_rows = []
    statement_calculation_rows = []
    link_rows = []
    script_db_rows = []
    script_schema_rows = []
    script_task_rows = []
    
    for ev in events:
        (
            dataset_set,
            dataelement_set,
            data_source_set,
            resource_set,
            dataset_lineage_set,
            data_element_lineage_set,
            statement_set,
            statement_calculation_set,
            statement_calculation_relationship_set,
            script_db_id,
            script_schema_id,
            script_task_id,
        ) = process_lineage_event(ev)
        dataset_set_master.update(dataset_set)
        dataelement_set_master.update(dataelement_set)
        data_source_set_master.update(data_source_set)
        resource_set_master.update(resource_set)
        dataset_lineage_set_master.update(dataset_lineage_set)
        data_element_lineage_set_master.update(data_element_lineage_set)
        statement_set_master.update(statement_set)
        statement_calculation_set_master.update(statement_calculation_set)
        statement_calculation_relationship_set_master.update(statement_calculation_relationship_set)
        script_db_master.add(script_db_id)
        script_schema_master.add(script_schema_id)
        script_tasks_master.add(script_task_id)

    # Processing Script Reference Resource with parent to $ as required by CDGC
    for rs_id in sorted(resource_set_master):
        resource_rows.append((rs_id, "TRUE", "", rs_id))
        link_rows.append(("$resource",rs_id, "core.ResourceParentChild"))
    
    # Processing Script Placeholder database
    for sdb_id in sorted(script_db_master):
        script_db_rows.append((sdb_id, "", "TRUE", sdb_id))
        # Processing Script Placeholder database with parent to $
        link_rows.append(("$resource", sdb_id, "core.ResourceParentChild"))
    # Processing Script Placeholder schema
    for script_s_id in sorted(script_schema_master):
        parent_id, schema_name = script_s_id.rsplit("/", 1)
        script_schema_rows.append((script_s_id, "", "TRUE", schema_name))
        # Process Script Db->Schema relationship
        link_rows.append((parent_id, script_s_id, "com.infa.odin.models.relational.DatabaseToSchema"))

    # Processing Script placeholder tasks
    for task_id in sorted(script_tasks_master):
        parent_id, task_name = task_id.rsplit('/', 1)
        script_task_rows.append((task_id, "", "TRUE", task_name))
        # Process Script Schema->Task relationship
        link_rows.append((parent_id, task_id, "com.infa.odin.models.relational.SchemaToTask"))

    # Processing Script Statement
    for statement_tuple in sorted(statement_set_master):
        statement_id, is_reference, is_assignable, statement_name, statement_details = statement_tuple
        parent_task_id, _ = statement_id.rsplit('/', 1)
        statement_rows.append((statement_id, is_reference, is_assignable, statement_name, statement_details))
        # Process Script Task -> Statement relationship
        link_rows.append((parent_task_id, statement_id, "com.infa.odin.models.relational.TaskToStatement"))

    # Processing Script Calculations
    for calculation_id in sorted(statement_calculation_set_master):
        _, calculation_name = calculation_id.rsplit('/', 1)
        statement_calculation_rows.append((calculation_id, "", "TRUE", calculation_name))

    # Processing Statement -> Calculation relationships
    for statement_id, calculation_id in sorted(statement_calculation_relationship_set_master):
        link_rows.append((statement_id, calculation_id, "com.infa.odin.models.relational.StatementToCalculation"))

    # Start processing datasources
    for dsrc_id in sorted(data_source_set_master):
        data_source_rows.append((dsrc_id, "TRUE", "", dsrc_id))
        parent_id, _ = dsrc_id.split('.', 1)
        # Processing Resource->DataSource Parentship
        link_rows.append((parent_id, dsrc_id, "core.ResourceParentChild"))
    
    # Processing dataset
    for ds_id in sorted(dataset_set_master):
        parent_id, ds_name = ds_id.rsplit('/', 1)
        dataset_rows.append((ds_id, "TRUE", "", ds_name))
        # Processing DataSource->Dataset Parentship
        link_rows.append((parent_id, ds_id, "core.DataSourceParentChild"))

    # Processing data elements
    for de_id in sorted(dataelement_set_master):
        parent_id, de_name = de_id.rsplit('/', 1)
        dataelement_rows.append((de_id, "TRUE", "", de_name))
        # Processing Dataset->Data Element Parentship
        link_rows.append((parent_id, de_id, "core.DataSetToDataElementParentship"))

    # Processing DataSet->DataSet lineage
    for ds_lineage_tuple in sorted(dataset_lineage_set_master):
        src, tgt = ds_lineage_tuple
        link_rows.append((src, tgt, "core.DataSetDataFlow"))

    # Processing DataElement -> Calculation -> DataElement lineage
    for data_element_lineage_tuple in sorted(data_element_lineage_set_master):
        src, tgt = data_element_lineage_tuple
        link_rows.append((src, tgt, "core.DirectionalDataFlow"))

    write_reference_assets_to_csv(out_dir, 'com.infa.odin.models.relational.Database.csv', script_db_rows)
    write_reference_assets_to_csv(out_dir, 'com.infa.odin.models.relational.Schema.csv', script_schema_rows)
    write_reference_assets_to_csv(out_dir, 'com.infa.odin.models.relational.Task.csv', script_task_rows)
    write_reference_assets_to_csv(out_dir, 'core.Resource.csv', resource_rows)
    write_reference_assets_to_csv(out_dir, 'core.DataSource.csv', data_source_rows)
    write_reference_assets_to_csv(out_dir,'core.DataSet.csv', dataset_rows)
    write_reference_assets_to_csv(out_dir,'core.DataElement.csv', dataelement_rows)
    write_reference_assets_to_csv(out_dir, 'com.infa.odin.models.relational.Statement.csv', statement_rows, header=['core.externalId', 'core.reference', 'core.assignable', 'core.name', 'com.infa.odin.models.relational.sourceStatementText'])
    write_reference_assets_to_csv(out_dir, 'com.infa.odin.models.relational.Calculation.csv', statement_calculation_rows)
    write_reference_assets_to_csv(out_dir, 'links.csv', link_rows, header=['Source', 'Target', 'Association'])
    zip_path = create_output_zip(out_dir)

    print(f"Processed {len(events)} lineage events from {len(input_files)} input file(s).")
    print(f"Wrote CSV output to {out_dir}.")
    print(f"Wrote zip package to {zip_path}.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True, help="Path to a JSONL/NDJSON file or directory of files")
    ap.add_argument("--out", dest="out_dir", required=True, help="Output directory")
    args = ap.parse_args()

    convert(args.in_path, args.out_dir)


if __name__ == "__main__":
    main()
