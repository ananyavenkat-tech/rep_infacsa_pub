import csv
from sqllineage.exceptions import SQLLineageException
from sqllineage.runner import LineageRunner
from sqllineage.core.metadata.dummy import DummyMetaDataProvider
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


def convert_lineage_tuple_to_csv(lineage, src_resource_id, tgt_resource_id, default_src_db, default_tgt_db, statement_id):
    # Collect Resource, Data Source, DataSets, and DataElements as per SQL statement
    dataset_set = set()
    dataelement_set = set()
    data_source_set = set()
    dataset_lineage_set = set()
    data_element_lineage_set = set()
    statement_calculation_set = set()

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
        src_ds_id =  f"{src_resource_id}.{src_db}"
        tgt_ds_id =  f"{tgt_resource_id}.{tgt_db}"
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

        # Insert lineage with calculation
        # insert lineage tuple between src->statement
        # insert lineage tuple between statement->target
        data_element_lineage_set.add((src_de_id, statement_cal_id))
        data_element_lineage_set.add((statement_cal_id, tgt_de_id))

    return dataset_set, dataelement_set, data_source_set, dataset_lineage_set, data_element_lineage_set, statement_calculation_set


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
                    tgt_tbl_id = f"{target_resource_nm}.{tbl_name_with_schema}"
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
                        tbl_id = f"{src_resource_nm}.{tbl_name_with_schema}"
                        # Add source table to master set
                        dataset_master_set.add(tbl_id)
                        # Add table level lineage from source table to target table, add statement as the joint component
                        dataset_lineage_master_set.add((tbl_id, statement_id))
                        dataset_lineage_master_set.add((statement_id, tgt_tbl_id))
                    
                    if len(runner.source_tables) == 0:
                        # This means the code itself is creating a new table
                        # Example is:  CREATE OR REPLACE TEMPORARY VIEW new_tmp_view AS SELECT CURRENT_DATE() AS RUN_DATE
                        dataset_lineage_master_set.add((statement_id, tgt_tbl_id))

                # Now processing column level lineage
                if len(column_lineages) != 0:
                    col_lineage_count += 1
                    dataset_set, dataelement_set, data_source_set, ds_link, de_link, statement_cal_set = convert_lineage_tuple_to_csv(
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
                unparsed_sql
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
        link_rows = []
        data_set_rows = []
        data_element_rows = []
        statement_rows = []
        statement_calculation_rows = []

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
            unparsed_sql
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

        # Resource CSV entries (single resource assumed)
        resource_rows = [
            (src_resource_nm, "TRUE", "", src_resource_nm), 
            (target_resource_nm, "TRUE", "", target_resource_nm)
        ]
        
        # Add script database placeholder
        script_db_rows = [(script_db, "", "TRUE", script_db)]

        # Add script schema placeholder
        script_schema_rows = [(script_schema_id, "", "TRUE", script_schema)]

        # Add script task row (should be 1 row per task, based on the provided task name)
        script_task_rows = [(task_id, "", "TRUE", task_name)]

        # Add reference data source rows
        data_source_rows = [(ds_id, "TRUE", "", ds_id) for ds_id in data_source_master_set]

        # Add init links including:
        #      1. $resource (required value for CDGD) -> resources
        #      2. resource -> data sources also additional entries
        #      3. resource -> script database
        link_rows.extend([
            ('$resource', src_resource_nm, "core.ResourceParentChild"), 
            ('$resource', target_resource_nm, "core.ResourceParentChild"),
            ('$resource', script_db, "core.ResourceParentChild"),
            (src_resource_nm, f"{src_resource_nm}.{source_database}", "core.ResourceParentChild"),
            (target_resource_nm, f"{target_resource_nm}.{target_database}", "core.ResourceParentChild")
        ])

        for datasource_id in data_source_master_set:
            resource_id, _ = datasource_id.rsplit('.', 1)
            link_rows.append((resource_id, datasource_id, "core.ResourceParentChild"))

        # Add script place holder database -> schema relationship
        link_rows.append((script_db, script_schema_id, "com.infa.odin.models.relational.DatabaseToSchema"))

        # Add script placeholder schema -> task relationship
        link_rows.append((script_schema_id, task_id, "com.infa.odin.models.relational.SchemaToTask"))

        # Creating entries for each table, and add parent data source as the relationship
        for ds_id in dataset_master_set:
            data_source, tbl_name = ds_id.rsplit('/', 1)
            # create core.DataSet.csv entry
            data_set_rows.append((ds_id, "TRUE", "", tbl_name))
            # create parent-child link from data source to data set
            link_rows.append((data_source, ds_id, "core.DataSourceParentChild"))

        # Add all data elements into data element rows for persisting into file
        for de_id in dataelement_master_set:
            _, col_name = de_id.rsplit('/', 1)
            if col_name == "*":
                # column level lineage should not containing straight * anymore
                continue
            data_element_rows.append((de_id, "TRUE", "", col_name))

        # Add all statements into statement rows
        # Add relationships between statements and task
        for statement_id, detail in statement_master_dict.items():
            statement_rows.append((statement_id, "", "TRUE", detail['name'], detail['sql']))
            link_rows.append((task_id, statement_id, "com.infa.odin.models.relational.TaskToStatement"))

        # Add all calculation(fields involved within statement) into calculation rows
        for cal_id in statement_cal_master_set:
            _, cal_name = cal_id.rsplit('/', 1)
            if cal_name == "*":
                # column level lineage should not containing straight * anymore
                continue
            statement_calculation_rows.append((cal_id, "", "TRUE", cal_name))

        # Statement to Calculation relationship entries
        for statement_id, cal_id in statement_calculation_relationship_master_set:
            link_rows.append((statement_id, cal_id, "com.infa.odin.models.relational.StatementToCalculation"))

        # DataSet to DataElement Parentship entries
        for src_ds, tgt_de in dataset_dataelement_relationship_master_set:
            link_rows.append((src_ds, tgt_de, "core.DataSetToDataElementParentship"))

        # Table level lineage entries
        for src_ds, tgt_ds in dataset_lineage_master_set:
            link_rows.append((src_ds, tgt_ds, "core.DataSetDataFlow"))

        # Data Element level lineage entries
        for src_de, tgt_de in data_element_lineage_master_set:
            link_rows.append((src_de, tgt_de, "core.DirectionalDataFlow"))

        return {
            "resource_rows": resource_rows,
            "script_db_rows": script_db_rows,
            "script_schema_rows": script_schema_rows,
            "script_task_rows": script_task_rows,
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