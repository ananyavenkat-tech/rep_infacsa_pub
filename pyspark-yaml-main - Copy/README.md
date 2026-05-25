# Informatica CDGC Custom SQL Scanner

This project provides a custom metadata extraction utility for SQL scripts, specifically designed for ingestion into Informatica Cloud Data Governance and Catalog (CDGC).

## Features
- **Hierarchical ID Synchronization**: Ensures all objects (Tasks, Statements, Calculations, Tables, Columns) are correctly linked for 100% publication success.
- **Reference Catalog Source Integration**: Automatically maps external data sources (e.g., Snowflake, SAP HANA, Databricks) as Reference Assets in CDGC.
- **End-to-End Lineage**: Captures full lineage from source table columns through SQL transformations (Calculations) to target datasets.

## Setup & Requirements
1. **Python 3.10+**: Ensure Python is installed.
2. **Dependencies**:
   ```bash
   pip install sqllineage
   ```

## Usage
1. Place your SQL scripts in the `input/` directory.
2. Run the extraction script:
   ```bash
   python parse_sql_script_repo.py -i input -o output
   ```
3. The script will generate a set of CSV files in the `output/` directory and a final ZIP package.

## CDGC Ingestion Steps
1. Log in to **Informatica Cloud (IICS)**.
2. Open **Metadata Command Center**.
3. Create a **New Catalog Source** using the **Custom Metadata Integration** (Custom CSV) type.
4. Name the resource: `SOCAR_SQLScript_CL`.
5. Upload the `CDGC_Metadata_Package.zip` from the `output/` folder.
6. Run the extraction.

## Project Structure
- `input/`: Source SQL files.
- `output/`: Generated CSVs and final ZIP package.
- `parse_column_lineage.py`: Core logic for hierarchical ID generation and lineage mapping.
- `parse_sql_script_repo.py`: Main driver script for processing multiple SQL files.
