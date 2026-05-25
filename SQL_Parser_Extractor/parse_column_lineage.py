import csv
import re
# pyrefly: ignore [missing-import]
from sqllineage.exceptions import SQLLineageException # pyrefly: ignore [missing-import]
from sqllineage.runner import LineageRunner # pyrefly: ignore [missing-import]
from sqllineage.core.metadata.dummy import DummyMetaDataProvider # pyrefly: ignore [missing-import]
from parser_utils import update_master_tbl_col_set, convert_dataset_column_relationship_to_metadata_feed, merge_table_dicts, dicts_are_equal_ignore_order, get_logger, convert_star_syntax_to_special_reference

logging = get_logger(__name__)

def get_parent_asset(col_str):
    if col_str.parent is None:
      for parent in col_str.parent_candidates:
         if parent.__class__.__name__ != "SubQuery":
            return parent
    else:
      return col_str.parent

def parse_column(col_str, default_db="<default>"):
    parent = get_parent_asset(col_str)
    if parent.__class__.__name__ == "SubQuery":
       raise SQLLineageException("Incomplete lineages causing subquery to be parent of column. Please check the lineage data.")
    schema = str(parent.schema).strip()
    if schema == "<default>":
        schema = default_db
    table = str(parent.raw_name).strip()
    column = str(col_str.raw_name).strip()
    return schema, table, column

def transform_id(original_id):
    """
    Transform the original ID to a format compatible with CDGC.
    Ensures consistency between Table and Column IDs by preserving the resource root
    and flattening the remaining path.
    """
    if not original_id or original_id == '$resource': return original_id
    parts = original_id.split('/')
    if len(parts) <= 1: return original_id
    
    # Resource / FlattenedPath
    return parts[0] + "/" + "_".join(parts[1:])

def transform_name(original_id, is_resource=False):
    """
    Transforms Name to CDGC format.
    """
    if not original_id: return original_id
    dotted = original_id.replace('/', '.')
    return dotted

def sanitize_custom_id(value):
    """Build stable flat IDs for custom model assets."""
    cleansed = re.sub(r'[^A-Za-z0-9]+', '_', str(value)).strip('_')
    return cleansed.upper() or "UNNAMED"


def convert_lineage_tuple_to_csv(lineage, src_resource_id, tgt_resource_id, default_src_db, default_tgt_db, statement_id):
    # Collect Resource, Data Source, DataSets, and DataElements as per SQL statement
    dataset_set = set()
    dataelement_set = set()
    data_source_set = set()
    dataset_lineage_set = set()
    data_element_lineage_set = set()
    dataset_lineage_set = set()
    direct_lineage_set = set()
    direct_dataset_lineage_set = set()
    statement_calculation_set = set()
    dataset_statement_lineage_set = set()

    for lineage_tuple in lineage:
        src_col = lineage_tuple[0]
        # intermediate column ignored for output IDs
        tgt_col = lineage_tuple[-1]
        src_db, src_table, src_column = parse_column(src_col, default_src_db)
        tgt_db, tgt_table, tgt_column = parse_column(tgt_col, default_tgt_db)
        if len(lineage) > 1 and src_column == "*" and tgt_column == "*":
            # "select *"" has been resolved as lineage list has more than 1 lineage tuple
            # (as there are other lineage relationship), then skip this *->* lineage tuple
            # but if below situations are valid:
            #      1. src_column is * but target is not, e.g. count(*) -> customer_count
            #      2. src_column is * and target is * but this is the only lineage (len(lineage) == 1)
            # hence only below situations are considered incorrect and skip:
            #      - source and target column of the lineage are both *, but there are other lineages tuples exist.
            continue
        src_ds_id =  f"{src_resource_id}/{src_db.replace('.', '/')}"
        tgt_ds_id =  f"{tgt_resource_id}/{tgt_db.replace('.', '/')}"
        data_source_set.add(src_ds_id)
        data_source_set.add(tgt_ds_id)

        src_dataset_id = f"{src_ds_id}/{src_table}"
        tgt_dataset_id = f"{tgt_ds_id}/{tgt_table}"

        dataset_set.add(src_dataset_id)
        dataset_set.add(tgt_dataset_id)

        src_de_id = f"{src_dataset_id}/{src_column}"
        if src_column == "*":
            src_de_id = f"{src_dataset_id}/{convert_star_syntax_to_special_reference(src_de_id)}"
        tgt_de_id = f"{tgt_dataset_id}/{tgt_column}"
        if tgt_column == "*":
            tgt_de_id = f"{tgt_dataset_id}/{convert_star_syntax_to_special_reference(tgt_de_id)}"
            statement_cal_id = f"{statement_id}/{convert_star_syntax_to_special_reference(tgt_de_id)}"
        else:
            statement_cal_id = f"{statement_id}/{tgt_column}"


        dataelement_set.add(src_de_id)
        dataelement_set.add(tgt_de_id)
        statement_calculation_set.add(statement_cal_id)

        # Track direct lineage
        direct_lineage_set.add((src_de_id, tgt_de_id))
        direct_dataset_lineage_set.add((src_dataset_id, tgt_dataset_id))

        # Insert lineage with calculation
        # insert lineage tuple between src->statement
        # insert lineage tuple between statement->target
        data_element_lineage_set.add((src_de_id, statement_cal_id))
        data_element_lineage_set.add((statement_cal_id, tgt_de_id))

        # Insert dataset level lineage to statement
        dataset_statement_lineage_set.add((src_dataset_id, statement_id))
        dataset_statement_lineage_set.add((statement_id, tgt_dataset_id))

    return dataset_set, dataelement_set, data_source_set, dataset_lineage_set, data_element_lineage_set, statement_calculation_set, dataset_statement_lineage_set, direct_lineage_set, direct_dataset_lineage_set


# Main function to process sql statements.
# Task name extracted from the YAML file.
# Take source/target:
#           - default database names
#           - default resources names
# Input metadata repository:
#           - dataset-column relationship
# Iteration control as this is an recursive process
def process_sql_statements(sql_statements, src_resource_nm, target_resource_nm, source_database, target_database, metadata, iteration=1, task_name='default', dialect="ansi"):
        parsed_count = 0
        col_lineage_count = 0
        unparsed_count = 0

        dataset_master_set = set()
        dataelement_master_set = set()
        data_source_master_set = set()
        dataset_lineage_master_set = set()
        data_element_lineage_master_set = set()
        dataset_dataelement_relationship_master_set = set()
        statement_cal_master_set = set()
        statement_master_dict = {}
        statement_calculation_relationship_master_set = set()
        dataset_statement_lineage_master_set = set()
        direct_lineage_master_set = set()
        direct_dataset_lineage_master_set = set()
        unparsed_sql = set()

        simple_metadata_provider = DummyMetaDataProvider(metadata)

        # parsing each sql statement
        for idx, sql in enumerate(sql_statements):
            try:
                runner = LineageRunner(sql=sql, dialect=dialect, metadata_provider=simple_metadata_provider)
                column_lineages = runner.get_column_lineage()
                # Add table level lineage first
                if len(runner.target_tables) == 0:
                    # If no target tables, it means the SQL could not be parsed
                    unparsed_count += 1
                    logging.warning(f"[iter-{iteration}]SQL Statement #{idx + 1} failed: {sql}")
                    unparsed_sql.add((task_name, sql))
                elif len(runner.target_tables) > 1:
                    # If multiple target tables, we cannot determine lineage accurately
                    logging.warning(f"[iter-{iteration}]SQL statement #{idx + 1} has multiple target tables, lineage may not be accurate.")
                    logging.warning(f"SQL: {sql}")
                    unparsed_sql.add((task_name, sql))
                else:
                    # The Main parsing path
                    # If we have a target table, we can process lineage
                    # Create a statement object for the SQL statement
                    statement_id = f"{task_name}/statement_{idx + 1}"
                    statement_master_dict[statement_id] = {
                        'name': f"statement_{idx + 1}",
                        'sql': sql
                    }
                    # Process table level lineage, add target table to master set with columns
                    tbl_name_with_schema = str(runner.target_tables[0])
                    # Consume metadata to Table/Column/Table-Column relationship reference
                    dataset_dataelement_relationship_master_set, dataelement_master_set = update_master_tbl_col_set(
                        dataset_dataelement_relationship_master_set, 
                        dataelement_master_set, 
                        target_resource_nm, 
                        tbl_name_with_schema,
                        metadata)
                    
                    tbl_name_with_schema = tbl_name_with_schema.replace(".", "/")
                    tgt_tbl_id = f"{target_resource_nm}/{tbl_name_with_schema}"
                    # Add target table to master set
                    dataset_master_set.add(tgt_tbl_id)
                    # For each source table, add to master set with columns
                    # Then add table lineage from each source table to target table
                    for tbl in runner.source_tables:
                        tbl_name_with_schema = str(tbl)
                        dataset_dataelement_relationship_master_set, dataelement_master_set = update_master_tbl_col_set(
                            dataset_dataelement_relationship_master_set, 
                            dataelement_master_set, 
                            src_resource_nm, 
                            tbl_name_with_schema,
                            metadata)
                        tbl_name_with_schema = tbl_name_with_schema.replace(".", "/")
                        tbl_id = f"{src_resource_nm}/{tbl_name_with_schema}"
                        # Add source table to master set
                        dataset_master_set.add(tbl_id)
                        # Add table level lineage from source table to target table, add statement as the joint component
                        dataset_statement_lineage_master_set.add((tbl_id, statement_id))
                        dataset_statement_lineage_master_set.add((statement_id, tgt_tbl_id))
                    
                    if len(runner.source_tables) == 0:
                        # This means the code itself is creating a new table
                        # Example is:  CREATE OR REPLACE TEMPORARY VIEW new_tmp_view AS SELECT CURRENT_DATE() AS RUN_DATE
                        dataset_statement_lineage_master_set.add((statement_id, tgt_tbl_id))

                # Now processing column level lineage
                if len(column_lineages) != 0:
                    col_lineage_count += 1
                    (
                        dataset_set, 
                        dataelement_set, 
                        data_source_set, 
                        ds_link, 
                        de_link, 
                        statement_cal_set, 
                        ds_stmt_link,
                        direct_link,
                        direct_ds_link
                    ) = convert_lineage_tuple_to_csv(
                        column_lineages, 
                        src_resource_nm, 
                        target_resource_nm, 
                        source_database, 
                        target_database,
                        statement_id
                        )
                    dataset_master_set.update(dataset_set)
                    dataelement_master_set.update(dataelement_set)
                    data_source_master_set.update(data_source_set)
                    dataset_lineage_master_set.update(ds_link)
                    data_element_lineage_master_set.update(de_link)
                    statement_cal_master_set.update(statement_cal_set)
                    dataset_statement_lineage_master_set.update(ds_stmt_link)
                    direct_lineage_master_set.update(direct_link)
                    direct_dataset_lineage_master_set.update(direct_ds_link)
                else:
                    logging.warning(f"[iter-{iteration}] SQL Statement #{idx + 1} has no column lineage: {sql}")
                parsed_count += 1
                
            except SQLLineageException as e:
                unparsed_count += 1
                logging.warning(f"[iter-{iteration}] SQL Statement #{idx + 1} failed: {sql}")
                unparsed_sql.add((task_name, sql))
        
        # Now the data elements list may contain additional column details which is not provided in the yaml,
        # re-iterate through the dataelement_master_set to update dataset_dataelement_relationship_master_set
        for de_id in dataelement_master_set:
            parent_id, col_name = de_id.rsplit('/', 1)
            if col_name == "*":
                special_de_reference = convert_star_syntax_to_special_reference(de_id)
                logging.warning(f"[iter-{iteration}] Wrapping DataElement {de_id} as [{special_de_reference}] since it has no column name.")
                dataset_dataelement_relationship_master_set.add((parent_id, special_de_reference))
            else:
                dataset_dataelement_relationship_master_set.add((parent_id, de_id))

        # Processing calculation and statement relationships
        for cal_id in statement_cal_master_set:
            # cal_id is in the format of "statement_id/calculation_id"
            statement_id, cal_name = cal_id.rsplit('/', 1)
            if cal_name == "*":
                special_de_reference = convert_star_syntax_to_special_reference(cal_id)
                logging.warning(f"[iter-{iteration}] Wrapping Calculation {cal_id} as [{special_de_reference}] since it has no name.")
                statement_calculation_relationship_master_set.add((statement_id, special_de_reference))
            else:
                statement_calculation_relationship_master_set.add((statement_id, cal_id))

        # Based on the parsed dataset-column relationships, create additional metadata
        # derived from the sql code.
        new_metadata = convert_dataset_column_relationship_to_metadata_feed(
            dataset_dataelement_relationship_master_set, 
            src_resource_nm, 
            target_resource_nm
        )

        # Merge original metadata, and new metadata together
        updated_metadata = merge_table_dicts([new_metadata, metadata])

        # Check if merged metadata is the same as the original
        if dicts_are_equal_ignore_order(updated_metadata, metadata):
            # If metadata has not changed, return the current state
            logging.info(f"[iter-{iteration}]No changes in metadata, returning final state.")
            return (
                parsed_count,
                col_lineage_count,
                unparsed_count,
                dataset_master_set,
                dataelement_master_set,
                data_source_master_set,
                dataset_lineage_master_set,
                data_element_lineage_master_set,
                dataset_dataelement_relationship_master_set,
                statement_master_dict,
                statement_cal_master_set,
                statement_calculation_relationship_master_set,
                unparsed_sql,
                dataset_statement_lineage_master_set,
                direct_lineage_master_set,
                direct_dataset_lineage_master_set
            )
        else:
            # if there are delta from the metadata, meaning new insight has been
            # added, we need to re-process the SQL statements with updated metadata
            logging.info(f"[iter-{iteration}] Metadata is updated with additional info, re-processing SQL statements with updated metadata within a new iteration")
            return process_sql_statements(
                sql_statements,
                src_resource_nm,
                target_resource_nm,
                source_database,
                target_database,
                updated_metadata,
                iteration + 1,
                task_name=task_name,
                dialect=dialect
            )


# Wrapper functions for the main sql parsing function, taking:
#     1. default configurations, mostly names for source/target databases, resources, script placeholder database and schemas
#     2. SQL statements in a list format to parse
#     3. metadata for involved tables (metadata in the format of {table:[col1, col2...]})
#     4. job related config like task name
def parse_sql_statements(
        sql_statements,
        metadata,
        default_value_config,
        task_name,
        dialect="ansi"
    ):
        src_resource_nm = default_value_config["src_resource_nm"]
        target_resource_nm = default_value_config["target_resource_nm"]
        source_database = default_value_config["source_database"]
        target_database = default_value_config["target_database"]
        script_db = default_value_config["script_db"]
        script_schema = default_value_config["script_schema"]

        # Defining structure placeholders (database and schema) for scripts
        script_schema_id = f"{script_db}/{script_schema}"
        task_id = f"{script_schema_id}/{task_name}"

        if not sql_statements:
            logging.error("No SQL statements provided.")
            return None
        
        # SQL parsing and lineage extraction main function call
        (
            parsed_count,
            col_lineage_count,
            unparsed_count,
            dataset_master_set,
            dataelement_master_set,
            data_source_master_set,
            dataset_lineage_master_set,
            data_element_lineage_master_set,
            dataset_dataelement_relationship_master_set,
            statement_master_dict,
            statement_cal_master_set,
            statement_calculation_relationship_master_set,
            unparsed_sql,
            dataset_statement_lineage_master_set,
            direct_lineage_master_set,
            direct_dataset_lineage_master_set
        ) = process_sql_statements(
            sql_statements,
            src_resource_nm,
            target_resource_nm,
            source_database,
            target_database,
            metadata,
            task_name=task_id,
            iteration=1,
            dialect=dialect
        )

        # --- core.DataSource.csv ---
        all_data_sources = set()
        for ds_id in data_source_master_set:
            if '/' in ds_id:
                all_data_sources.add(ds_id)

        # Helper for consistent hierarchical IDs
        # Anchor flattening at the DataSource/Schema level
        ds_ids = set(all_data_sources)
        ds_ids.add(script_schema_id)
        ds_ids.add(script_db)
        
        def get_cdgc_id(raw_id):
            if not raw_id or raw_id == '$resource': return raw_id
            if raw_id in ds_ids: return transform_id(raw_id)
            parts = raw_id.split('/')
            if len(parts) <= 1: return raw_id
            # Recursive: ParentID / Leaf
            parent_raw = '/'.join(parts[:-1])
            return f"{get_cdgc_id(parent_raw)}/{parts[-1]}"

        # --- core.Resource.csv ---
        resource_rows = []
        # Determine which resources are actually referenced by parsed datasets/data sources
        referenced_resources = set()
        for ds_id in dataset_master_set:
            if '/' in ds_id:
                referenced_resources.add(ds_id.split('/')[0])
        for ds_id in data_source_master_set:
            if '/' in ds_id:
                referenced_resources.add(ds_id.split('/')[0])

        # Only include default resources if they are actually referenced.
        unique_resources = set([src_resource_nm, target_resource_nm]) & referenced_resources
        for res_nm in sorted(unique_resources):
            res_id = transform_id(res_nm)
            resource_rows.append((res_id, transform_name(res_nm), "false", "TRUE"))

        # Track reference resources for data sources
        ds_to_ref_resource = {}
        ds_to_connection_id = {}

        # --- core.DataSource.csv ---
        data_source_rows = []

        all_data_sources_list = sorted(list(all_data_sources))
        f_script_schema_id = get_cdgc_id(script_schema_id)
        for ds_id in all_data_sources_list:
            f_ds_id = get_cdgc_id(ds_id)
            ds_name = ds_id.split('/')[-1]
            
            # Create a reference resource for this data source
            parts = ds_id.split('/')
            if len(parts) > 1:
                path_str = "_".join(parts[1:]).upper().replace("<", "").replace(">", "")
                ref_res_id = f"REFERENCE_{path_str}_CONN"
                conn_id = f"{ref_res_id}_DS"
                if ref_res_id not in [r[0] for r in resource_rows]:
                    # These ARE Reference assets
                    resource_rows.append((ref_res_id, ds_name.replace("<", "").replace(">", ""), "true", "TRUE"))
                ds_to_ref_resource[f_ds_id] = ref_res_id
                ds_to_connection_id[f_ds_id] = conn_id
                data_source_rows.append((conn_id, "TRUE", "TRUE", ds_name.replace("<", "").replace(">", "")))

        def get_published_id(raw_id):
            f_id = get_cdgc_id(raw_id)
            parts = f_id.split('/')
            if len(parts) <= 1:
                return f_id
            conn_id = ds_to_connection_id.get('/'.join(parts[:2]))
            if not conn_id:
                return f_id
            return '/'.join([conn_id] + parts[2:])

        parser_id = f"SQLPARSER_{sanitize_custom_id(task_name)}"
        custom_model_rows = [(parser_id, task_name, "", "TRUE", "", "", "")]

        statement_to_script_id = {}
        sql_script_rows = []
        for stmt_id, detail in statement_master_dict.items():
            script_id = f"SQLSCRIPT_{sanitize_custom_id(stmt_id)}"
            statement_to_script_id[stmt_id] = script_id
            sql_script_rows.append((
                script_id,
                detail['name'],
                "",
                "",
                "",
                "",
                detail['sql']
            ))

        calculation_to_column_id = {}
        sql_script_column_rows = []
        for idx, cal_id in enumerate(sorted(statement_cal_master_set), start=1):
            if cal_id.endswith('/*'):
                continue
            cal_name = cal_id.split('/')[-1]
            col_id = f"SQLSCRIPT_COLUMN_{idx}_{sanitize_custom_id(cal_id)}"
            calculation_to_column_id[cal_id] = col_id
            sql_script_column_rows.append((col_id, cal_name, "", "", "", "", "", "", "", "", ""))

        # Keep these empty to avoid publishing relational transformation assets
        # alongside the custom SQLScript model assets.
        script_db_rows = []
        script_schema_rows = []
        script_task_rows = []

        # --- core.DataSet.csv ---
        data_set_rows = []
        for ds_id in sorted(dataset_master_set):
            f_dataset_id = get_published_id(ds_id)
            data_set_rows.append((f_dataset_id, "TRUE", "TRUE", ds_id.split('/')[-1]))

        # --- core.DataElement.csv ---
        data_element_rows = []
        for de_id in sorted(dataelement_master_set):
            if de_id.endswith('/*'): continue
            f_de_id = get_published_id(de_id)
            data_element_rows.append((f_de_id, "TRUE", "TRUE", de_id.split('/')[-1]))

        # --- legacy relational rows disabled for the custom model output ---
        statement_rows = []
        statement_calculation_rows = []

        # Remove unused or undesired top-level resources explicitly
        resource_rows = [r for r in resource_rows if r[0] != 'SOCAR_SQLScript_CL']

        # --- links.csv ---
        link_rows = []

        # 2. Resource to DataSource (Direct mapping to Connection)
        def lineage_datasource_sort_key(ds_id):
            leaf = ds_id.split('/')[-1]
            return (leaf == "<default>", leaf.lower())

        for ds_id in sorted(all_data_sources_list, key=lineage_datasource_sort_key):
            f_ds_id = get_cdgc_id(ds_id)
            ref_res_id = ds_to_ref_resource.get(f_ds_id)
            conn_id = ds_to_connection_id.get(f_ds_id)
            if ref_res_id and conn_id:
                link_rows.append((ref_res_id, conn_id, "core.ResourceParentChild"))

        # Custom SQL parser hierarchy.
        link_rows.append(("$RESOURCE", parser_id, "core.ResourceParentChild"))
        for script_id in statement_to_script_id.values():
            link_rows.append((parser_id, script_id, "custom.sqlparser.SQLParserToSQLScript"))
        for cal_id, col_id in calculation_to_column_id.items():
            stmt_id = cal_id.rsplit('/', 1)[0]
            script_id = statement_to_script_id.get(stmt_id)
            if script_id:
                link_rows.append((script_id, col_id, "custom.sqlparser.SQLScriptToSQLScriptColumn"))

        # 3. Schema to Table (Direct)
        for ds_id in sorted(dataset_master_set):
            p_id = '/'.join(ds_id.split('/')[:-1])
            link_rows.append((get_published_id(p_id), get_published_id(ds_id), "core.DataSourceParentChild"))

        # 4. Table to Column (Direct)
        for de_id in sorted(dataelement_master_set):
            if de_id.endswith('/*'): continue
            p_id = '/'.join(de_id.split('/')[:-1])
            link_rows.append((get_published_id(p_id), get_published_id(de_id), "core.DataSetToDataElementParentship"))

        # 6. Dataset lineage through custom SQLScript assets.
        stmt_links_added = set()
        for src, tgt in sorted(list(dataset_statement_lineage_master_set)):
            f_src = statement_to_script_id.get(src, get_published_id(src))
            f_tgt = statement_to_script_id.get(tgt, get_published_id(tgt))
            link = (f_src, f_tgt, "core.DataSetDataFlow")
            if link not in stmt_links_added:
                link_rows.append(link); stmt_links_added.add(link)

        # 7. Column lineage through custom SQLScriptColumn assets.
        calc_links_added = set()
        for src_de, tgt_de in sorted(list(data_element_lineage_master_set)):
            f_src = calculation_to_column_id.get(src_de, get_published_id(src_de))
            f_tgt = calculation_to_column_id.get(tgt_de, get_published_id(tgt_de))
            link = (f_src, f_tgt, "core.DirectionalDataFlow")
            if link not in calc_links_added:
                link_rows.append(link); calc_links_added.add(link)

        return {
            "resource_rows": resource_rows,
            "script_db_rows": script_db_rows,
            "script_schema_rows": script_schema_rows,
            "script_task_rows": script_task_rows,
            "custom_model_rows": custom_model_rows,
            "sql_script_rows": sql_script_rows,
            "sql_script_column_rows": sql_script_column_rows,
            "data_source_rows": data_source_rows,
            "data_set_rows": data_set_rows,
            "data_element_rows": data_element_rows,
            "statement_rows": statement_rows,
            "statement_calculation_rows": statement_calculation_rows,
            "link_rows": link_rows,
            "unparsed_sql": unparsed_sql,
            "parsed_count": parsed_count,
            "col_lineage_count": col_lineage_count,
            "unparsed_count": unparsed_count,
            "sql_statements": sql_statements
        }
