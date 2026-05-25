#############################################################################
#
# Metadata Extraction and Transformation from SQL Statement files.
# This take below inputs:
#     -  SQL Scripts location (folder path expecting *.sql files in there)
#     -  [Optional]Metadata repository (YAML format with table-column relationships)
#
# Script will parse the SQL scripts leveraging a python package 'sqllineage'
#     -  This will allow us to extract lineage information from the SQL statements
#     -  the provided metadata of source tables/columns will be used to enable detail
#        column-level lineage tracking.
#     -  on top of provided table-column metadata details, script will try to
#        enrich the metadata repository with new table-column details parsed as part of
#        the SQL transformations analysis (e.g. metadata discovered during parsing, temp tables etc.).
#     -  the script will run in an recursive manner (multiple iterations) until the
#        no more additional metadata can be extracted. With sufficient context and metadata,
#        the script should complete with less iterations.
#
# After parsing, script will emit below output into the output folder follows Informatica
# custom scanner consumable format:
#     -  core.DataElement.csv file with Informatica CDGC Custom Scanner format for reference
#        columns derived from the SQL statements
#     -  core.DataSet.csv file with Informatica CDGC Custom Scanner format for reference
#        datasets(tables/views) derived from the SQL statements
#     -  core.Resource.csv file with Informatica CDGC Custom Scanner format for reference
#        resources derived from the SQL statements
#     -  core.DataSource.csv file with Informatica CDGC Custom Scanner format for reference
#        data sources derived from the SQL statements
# For the reference assets relationships, it is Resource->Source-DataSet->DataElement
#
# Additionally, the script will create a set of Database->Schema->Task->Statement->Calculation
# documents, which is used to represent the SQL code (Statement) and field lineage (Calculation)
# 
# All relationships including:
#    - Resource to DataSource (parent child)
#    - DataSource to DataSet (parent child)
#    - DataSet to DataElement (parent child)
#    - Database to Schema (parent child)
#    - Schema to Task (parent child)
#    - Task to Statement (parent child)
#    - Statement to Calculation (parent child)
# And lineage information including:
#    - Table level lineage (represented as core.DataSetDataFlow)
#    - Column level lineage (represented as core.DirectionalDataFlow)
# Are captured within the last generated link.csv file
#
#
# Error Handling
#    For certain type of queries, for example:
#            - 'select * ...' without context or multiple lineage targets
#            - 'drop table ...' which means nothing to lineage
#            ...
#    the script may not be able to determine the exact lineage information.
#    In such cases, the unparsed SQL statements will be logged into the error.csv file.
#    Please note that, the error.csv SHOULD NOT be included within the below zip file
#
#
# After these steps, user can package up all generated csv files as a single zip file.
# Zip files can be:
#        - Upload to the created custom scanner manually through the GUI interface of CDGC
#        - Placed under the secure agent's local path and schedule the custom scanner to 
#          ingest it
#############################################################################

import yaml
import time
import argparse
from parse_column_lineage import parse_sql_statements
from parser_utils import extract_metadata_from_yaml, write_reference_assets_to_csv, get_logger, zip_files
import logging
import os
import subprocess
import sys

class DialectHelpAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        result = subprocess.run(['sqllineage', '--dialects'], capture_output=True, text=True)
        print(result.stdout)
        parser.exit()  # This exits without checking other arguments.

logger = get_logger(__name__)

def preprocess_sql(sql_content):
    """
    Preprocesses SQL content to remove dialect-specific syntax that may cause parsing issues.
    This ensures compatibility with standard SQL parsers.
    
    Removes:
    - SECURITY INVOKER (Databricks-specific)
    - RETURNS STRUCT and other Databricks UDF syntax
    - Other dialect-specific modifiers that aren't standard SQL
    """
    import re
    
    # Remove SECURITY INVOKER clause (Databricks-specific)
    sql_content = re.sub(r'\bSECURITY\s+INVOKER\s+', '', sql_content, flags=re.IGNORECASE)
    
    # Remove RETURNS clause with STRUCT (Databricks UDF syntax)
    sql_content = re.sub(r'\bRETURNS\s+STRUCT\s*\([^)]*\)\s*', '', sql_content, flags=re.IGNORECASE)
    
    # Remove USING LANGUAGE clause (Databricks UDF syntax)
    sql_content = re.sub(r'\bUSING\s+LANGUAGE\s+\w+\s*', '', sql_content, flags=re.IGNORECASE)
    
    logger.debug("SQL preprocessing completed: removed dialect-specific syntax")
    return sql_content

def extract_sql_from_file(sql_file_content):
    """
    Extracts all SQL statements from file content.
    
    - Preprocesses the content to remove dialect-specific syntax
    - Splits statements by semicolon
    - Returns cleaned SQL statements ready for parsing
    """
    # Preprocess to remove dialect-specific syntax
    sql_content = preprocess_sql(sql_file_content)
    
    sql_statements = []
    # Split SQL statements by semicolon, ignoring empty statements
    statements = [stmt.strip() for stmt in sql_content.split(';') if stmt.strip()]
    sql_statements.extend(statements)
    return sql_statements

def main():
    parser = argparse.ArgumentParser(description="Parse extract SQL lineage metadata from a folder of script files in .sql format, then convert them into Informatica CDGC Custom Lineage csv files.")
    parser.add_argument('-i','--input', type=str, required=True, help='Path to the SQL scripts (*.sql format) folder.')
    parser.add_argument('-m','--metadata', type=str, help='Path to the YAML file containing metadata definitions. If not provided, it will use empty dictionary as default.')
    parser.add_argument('-o','--output', type=str, required=True, help='Path to the output directory for all generated csv files.')
    parser.add_argument('-d', '--dialect', type=str, default='ansi', help='SQL dialect to use for parsing (e.g., ansi, sparksql, actual support please check using command "sqllineage --dialects"). Default is ansi.')
    parser.add_argument('-e','--error', type=str, help='Path to the error.csv file which containing the unparsed SQL statements with corresponding task name. If not provided, unparsed sql will be printed within standard output.')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose logging for debugging.')
    parser.add_argument('-p', '--packup', action='store_true', help='Enable packaging of output files into a zip archive instead of individual csv files')
    parser.add_argument('--src_rsc_nm', type=str, default='SOCAR_SQLScript_CL', help='Default source resource name when not present in the code. Default value is "SOCAR_SQLScript_CL"')
    parser.add_argument('--tgt_rsc_nm', type=str, default='SOCAR_SQLScript_CL', help='Default target resource name when not present in the code. Default value is "SOCAR_SQLScript_CL"')
    parser.add_argument('--script_db', type=str, default='script_repo', help='Default database name when not present in the code. Default value is "script_repo"')
    parser.add_argument('--script_schema', type=str, default='script_schema', help='Default schema name when not present in the code. Default value is "script_schema"')
    parser.add_argument('--src_db', type=str, default='<default>', help='Default source database name when not present in the code. Default value is "<default>"')
    parser.add_argument('--tgt_db', type=str, default='<default>', help='Default target database name when not present in the code. Default value is "<default>"')
    parser.add_argument('--dialects', nargs=0, action=DialectHelpAction, help='Show all supported SQL dialects by sqllineage package and exit.')

    args = parser.parse_args()

    sql_files = args.input
    script_dialect = args.dialect
    metadata_yaml = args.metadata
    output_dir = args.output
    err_dir = args.error
    src_resource_nm = args.src_rsc_nm
    target_resource_nm = args.tgt_rsc_nm
    script_db = args.script_db
    script_schema = args.script_schema
    source_database = args.src_db
    target_database = args.tgt_db
    is_verbose = args.verbose
    is_packup = args.packup

    if is_verbose:
        logger.setLevel(logging.DEBUG)

    if err_dir == output_dir:
        logger.error("Error directory cannot be the same as output directory. Please provide a different path for errors.")
        exit(1)

    if metadata_yaml:
      with open(metadata_yaml, 'r') as f:
          data = yaml.safe_load(f)
          sources = data.get('metadata', {}).get('sources', [])
          metadata = extract_metadata_from_yaml(sources)
    else:
      metadata = {}
      sources = []

    master_col_lineage_count = 0
    master_parsed_count = 0
    master_unparsed_count = 0
    
    # Initialize variables for master sets/lists
    master_resource_rows = []
    master_script_db_rows = []
    master_script_schema_rows = []
    master_script_task_rows = []
    master_data_source_rows = []
    master_data_set_rows = []
    master_data_element_rows = []
    master_statement_rows = []
    master_statement_calculation_rows = []
    master_link_rows = []
    master_unparsed_sql = []
    
    # Load all SQL script files from the provided folder path
    for filename in sorted(os.listdir(sql_files)):
      if filename.endswith('.sql'):
        file_path = os.path.join(sql_files, filename)
        with open(file_path, 'r', encoding='utf-8') as sql_file:
          sql_text = sql_file.read()
          sql_statements = extract_sql_from_file(sql_text)
          result_rows = parse_sql_statements(
              sql_statements=sql_statements,
              metadata=metadata,
              default_value_config={
                  "src_resource_nm": src_resource_nm,
                  "target_resource_nm": target_resource_nm,
                  "source_database": source_database,
                  "target_database": target_database,
                  "script_db": script_db,
                  "script_schema": script_schema
              },
              task_name=filename,
              dialect=script_dialect
          )
          if result_rows is None:
              continue

          master_resource_rows.extend(result_rows["resource_rows"])
          master_script_db_rows.extend(result_rows["script_db_rows"])
          master_script_schema_rows.extend(result_rows["script_schema_rows"])
          master_script_task_rows.extend(result_rows["script_task_rows"])
          master_data_source_rows.extend(result_rows["data_source_rows"])
          master_data_set_rows.extend(result_rows["data_set_rows"])
          master_data_element_rows.extend(result_rows["data_element_rows"])
          master_statement_rows.extend(result_rows["statement_rows"])
          master_statement_calculation_rows.extend(result_rows["statement_calculation_rows"])
          master_link_rows.extend(result_rows["link_rows"])
          master_unparsed_sql.extend(result_rows["unparsed_sql"])
          
          master_parsed_count += result_rows["parsed_count"]
          master_col_lineage_count += result_rows["col_lineage_count"]
          master_unparsed_count += result_rows["unparsed_count"]

    # Start writing all corresponding rows to the corresponding files (ONLY ONCE)
    core_header = ['core.externalId', 'core.Reference', 'core.assignable', 'core.name', 'core.reference']
    relational_header = ['core.externalId', 'core.Reference', 'core.assignable', 'core.name']
    
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Database.csv', master_script_db_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Schema.csv', master_script_schema_rows + master_data_source_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Task.csv', master_script_task_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Table.csv', master_data_set_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Column.csv', master_data_element_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'core.Resource.csv', master_resource_rows, header=core_header)
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Statement.csv', master_statement_rows, header=['core.externalId', 'core.Reference', 'core.assignable', 'core.name', 'com.infa.odin.models.relational.sourceStatementText'])
    write_reference_assets_to_csv(output_dir, 'com.infa.odin.models.relational.Calculation.csv', master_statement_calculation_rows, header=relational_header)
    write_reference_assets_to_csv(output_dir, 'links.csv', master_link_rows, header=['Source', 'Target', 'Association'])
    if err_dir:
        write_reference_assets_to_csv(err_dir, 'errors.csv', master_unparsed_sql, header=['Task', 'SQL'])
    
    if is_packup:
        output_file_list = [
                  'com.infa.odin.models.relational.Database.csv',
                  'com.infa.odin.models.relational.Schema.csv',
                  'com.infa.odin.models.relational.Task.csv',
                  'com.infa.odin.models.relational.Statement.csv',
                  'com.infa.odin.models.relational.Calculation.csv',
                  'com.infa.odin.models.relational.Table.csv',
                  'com.infa.odin.models.relational.Column.csv',
                  'core.Resource.csv',
                  'links.csv'
              ]
        # Create a zip file with all the output files
        zip_files(output_dir, output_file_list, zip_name='output.zip')
        # Remove original files after zipping
        for file in os.listdir(output_dir):
            if file in output_file_list:
                os.remove(os.path.join(output_dir, file))

    # Print out execution stats
    logger.info(f"Total parsed SQL statements: {master_col_lineage_count}/{master_parsed_count}")
    logger.info(f"Total unparsed SQL statements: {master_unparsed_count}")
    logger.info("Total stats:")
    logger.info(f" * Provided datasets: {len(sources)}")
    logger.info(f" * Resources: {len(master_resource_rows)}")
    logger.info(f" * Data Sources: {len(master_data_source_rows)}")
    logger.info(f" * Data Sets: {len(master_data_set_rows)}")
    logger.info(f" * Data Elements: {len(master_data_element_rows)}")
    logger.info(f" * Links: {len(master_link_rows)}")

if __name__ == "__main__":
    start = time.perf_counter()
    main()
    end = time.perf_counter()
    logger.info(f"Execution time: {end - start} seconds")
