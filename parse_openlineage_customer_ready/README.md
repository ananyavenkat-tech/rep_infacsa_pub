# Parse OpenLineage to CDGC CSV

This project converts OpenLineage `.jsonl` / `.ndjson` events into Informatica CDGC custom lineage CSV files.

It supports:

- OpenLineage dataset lineage from input datasets to output datasets
- column-level lineage when `columnLineage` facets are present
- statement and calculation assets for relational lineage representation
- nested schema flattening, including `timeStamp` / `timestamp` normalization to `timestamp`
- automatic output zip creation

## Project Structure

```text
parse_openlineage/
|-- input/                         # OpenLineage .jsonl/.ndjson files to process
|-- output/                        # Generated CDGC CSV files and zip package
|-- sample_input/                  # Sample OpenLineage .ndjson files
|-- customer_package/              # Final customer shareable zip is created here
|-- parse_openlineage_jsonl.py      # Main OpenLineage parser
|-- parse_sql_script_repo.py        # SQL script folder parser
|-- parse_yaml.py                   # YAML lineage parser
|-- parse_column_lineage.py         # SQL column lineage helpers
|-- parser_utils.py                 # Shared CSV utilities
|-- metadata.yaml                   # Example metadata definition
|-- requirements                    # Python dependencies
`-- README.md
```

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements
```

## Run OpenLineage Parser

Put OpenLineage `.jsonl` or `.ndjson` files in `input/`.

Run all files in the input folder:

```powershell
python .\parse_openlineage_jsonl.py --in .\input --out .\output
```

Run one file:

```powershell
python .\parse_openlineage_jsonl.py --in .\input\events.ndjson --out .\output
```

The parser rewrites generated outputs on every run.

## Generated Files

The parser creates these CSV files in `output/`:

```text
core.Resource.csv
core.DataSource.csv
core.DataSet.csv
core.DataElement.csv
com.infa.odin.models.relational.Database.csv
com.infa.odin.models.relational.Schema.csv
com.infa.odin.models.relational.Task.csv
com.infa.odin.models.relational.Statement.csv
com.infa.odin.models.relational.Calculation.csv
links.csv
```

It also creates:

```text
output/cdgc_openlineage_output.zip
```

The zip contains the generated CSV files only.

## Lineage Model

Dataset-level lineage:

```text
input DataSet -> Statement -> output DataSet
```

Column-level lineage, when OpenLineage has `columnLineage` facets:

```text
Statement -> Calculation
source DataElement -> Calculation -> target DataElement
```

The key link associations are:

```text
core.ResourceParentChild
core.DataSourceParentChild
core.DataSetToDataElementParentship
core.DataSetDataFlow
core.DirectionalDataFlow
com.infa.odin.models.relational.DatabaseToSchema
com.infa.odin.models.relational.SchemaToTask
com.infa.odin.models.relational.TaskToStatement
com.infa.odin.models.relational.StatementToCalculation
```

## Nested Fields

For nested input schemas, the parser emits leaf field names rather than container names.

Example:

```text
response._element.timeStamp -> timestamp
response._element.tagName   -> tagName
```

This avoids creating top-level container fields like `request` or `response` as columns.

## Customer Package

The final shareable project zip is created in `customer_package/`.

The package contains:

- parser source files
- `README.md`
- `requirements`
- `metadata.yaml`
- `input/`
- `output/`
- `sample_input/`

Generated cache folders and old temporary zip files are excluded.

## Sample Input

Sample OpenLineage files are available in `sample_input/`.

Run them separately with:

```powershell
python .\parse_openlineage_jsonl.py --in .\sample_input --out .\sample_output
```

Keep sample files out of `input/` unless you intentionally want them included in the main output.

## Notes

- Files without both `inputs` and `outputs` are read but do not produce lineage.
- Files without `columnLineage` still produce dataset-level lineage.
- Column lineage accuracy depends on the quality of OpenLineage `schema` and `columnLineage` facets.
- For very large exports, the current implementation is set-based and memory-backed. It is suitable for small to medium batches; very large enterprise exports may need streaming/chunked output.
