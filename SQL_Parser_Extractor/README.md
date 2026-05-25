# Informatica CDGC Custom SQL Parser Scanner

This project generates Informatica CDGC Custom Metadata Integration CSV files for SQL script lineage. It reads `.sql` files, parses dataset and column lineage with `sqllineage`, maps the SQL transformation layer into a custom `custom.sqlparser` model, and emits CSV files plus an upload zip package.

The current implementation writes generated CSV files to `output/` and the upload zip to `package/`.

## Technology

- Python 3.10+
- `sqllineage` for SQL table and column lineage parsing
- `pyyaml` for optional metadata YAML input
- Python standard library modules for CSV writing, packaging, logging, and CLI argument parsing
- Informatica CDGC Custom Metadata Integration CSV format
- Informatica custom model JSON package: `custom.sqlparser.json`

## Custom Model

The custom model is defined in `custom.sqlparser.json`.

| Class | Superclass | Purpose |
| --- | --- | --- |
| `custom.sqlparser.SQLParser` | `core.DataSource` | One asset per input `.sql` file. |
| `custom.sqlparser.SQLScript` | `core.DataSet` | One asset per parsed SQL statement. Stores SQL text in `custom.sqlparser.sourceStatement`. |
| `custom.sqlparser.SQLScriptColumn` | `core.DataElement` | One asset per calculated output/expression column. |

The model defines these parent-child associations:

| Association | From | To |
| --- | --- | --- |
| `custom.sqlparser.SQLParserToSQLScript` | `SQLParser` | `SQLScript` |
| `custom.sqlparser.SQLScriptToSQLScriptColumn` | `SQLScript` | `SQLScriptColumn` |

Load or register this JSON model in CDGC before ingesting the generated CSV package.

## Lineage Design

The generated lineage follows this pattern:

```text
Reference Resource
  -> Reference DataSource
    -> Reference DataSet
      -> Reference DataElement

$RESOURCE
  -> SQLParser
    -> SQLScript
      -> SQLScriptColumn
```

Dataset-level lineage is written as `core.DataSetDataFlow`:

```text
source DataSet -> SQLScript -> target DataSet
```

Column-level lineage is written as `core.DirectionalDataFlow`:

```text
source DataElement -> SQLScriptColumn -> target DataElement
```

This is the key difference from the older relational `Database -> Schema -> Task -> Statement -> Calculation` structure. The current scanner no longer publishes those relational transformation assets.

## Project Structure

```text
.
|-- custom.sqlparser.json
|-- input/
|   |-- countstarexample.sql
|   |-- create_simpfy_views.sql
|   `-- openlineage-event.jsonl
|-- metadata.yaml
|-- output/
|-- package/
|-- parse_column_lineage.py
|-- parse_sql_script_repo.py
|-- parser_utils.py
|-- requirements.txt
`-- README.md
```

Important files:

- `parse_sql_script_repo.py`: Main CLI driver. Reads SQL files, calls the parser, writes CSVs, and creates `CustomLineage.zip`.
- `parse_column_lineage.py`: Core parsing and mapping logic. Builds reference assets, custom SQL parser assets, parent-child links, dataset lineage, and column lineage.
- `parser_utils.py`: Shared helpers for metadata conversion, CSV writing, logging, and zip creation.
- `custom.sqlparser.json`: Custom model package required by CDGC.
- `metadata.yaml`: Optional example metadata for known table/column relationships.
- `input/`: SQL inputs. Only `.sql` files are parsed; other files are ignored.
- `output/`: Generated CSV files.
- `package/`: CDGC upload package, `CustomLineage.zip`.

## Setup

Create or activate a Python environment, then install dependencies:

```bash
pip install -r requirements.txt
```

Check supported SQL dialects:

```bash
python parse_sql_script_repo.py --dialects
```

## Generate CSV And Package

Run the scanner:

```bash
python parse_sql_script_repo.py -i input -o output
```

This creates:

```text
output/*.csv
package/CustomLineage.zip
```

Skip package creation only when you need CSV-only output:

```bash
python parse_sql_script_repo.py -i input -o output --no-packup
```

Use a specific SQL dialect if needed:

```bash
python parse_sql_script_repo.py -i input -o output --dialect sparksql
```

Use optional metadata:

```bash
python parse_sql_script_repo.py -i input -m metadata.yaml -o output
```

Use a custom package folder or zip file name:

```bash
python parse_sql_script_repo.py -i input -o output --package-dir package --zip-name CustomLineage.zip
```

## Generated CSV Files

The scanner writes these files:

| File | Description |
| --- | --- |
| `core.Resource.csv` | Reference catalog resources discovered from SQL object names. |
| `core.DataSource.csv` | Reference data sources under each resource. |
| `core.DataSet.csv` | Reference tables/views. |
| `core.DataElement.csv` | Reference columns. |
| `custom.sqlparser.SQLParser.csv` | One parser asset per SQL file. |
| `custom.sqlparser.SQLScript.csv` | One SQL script asset per SQL statement. |
| `custom.sqlparser.SQLScriptColumn.csv` | One transformation column asset per calculated output column. |
| `links.csv` | Parent-child, dataset lineage, and column lineage associations. |

Expected working association types include:

- `core.ResourceParentChild`
- `core.DataSourceParentChild`
- `core.DataSetToDataElementParentship`
- `custom.sqlparser.SQLParserToSQLScript`
- `custom.sqlparser.SQLScriptToSQLScriptColumn`
- `core.DataSetDataFlow`
- `core.DirectionalDataFlow`

## CDGC Ingestion Steps

1. Register or deploy `custom.sqlparser.json` in the CDGC environment.
2. Create or open the Custom Metadata Integration catalog source.
3. Upload `package/CustomLineage.zip`.
4. Run extraction.
5. Validate that assets ingest and lineage appears through:
   - dataset to SQL statement to dataset
   - data element to SQL script column to data element

## Validation

After generation, a healthy run should have no missing link endpoints and no row-width mismatch across CSV files. The current output package was validated with:

```text
custom.sqlparser.SQLScriptColumn.csv header 11 rows 71 bad 0
links.csv header 3 rows 384 bad 0
missing linked ids: 0
core.DataSetDataFlow: 8
core.DirectionalDataFlow: 142
custom.sqlparser.SQLParserToSQLScript: 4
custom.sqlparser.SQLScriptToSQLScriptColumn: 71
```

## Notes

- `$RESOURCE` is only used in `links.csv` as the scanner root. It is not published as a row in `core.Resource.csv`.
- The parser intentionally keeps reference resources separate from SQL parser transformation assets.
- `custom.sqlparser.SQLScriptColumn.csv` must have exactly 11 columns because it follows the model template columns expected by CDGC.
- Non-SQL files in `input/` are ignored by the scanner.
