import csv
import os
import logging
import zipfile

# convert all * column name into table.* for clear indication
def convert_star_syntax_to_special_reference(col_id):
    tab_id, _ = col_id.rsplit('/', 1)
    _, table_name = tab_id.rsplit('/', 1)
    return f"{table_name}.*"

def get_logger(logger_module_name, level=logging.INFO):
    # Create a logger for module/script
    logger = logging.getLogger(logger_module_name)
    logger.setLevel(level)  # Set the desired level

    # Create a handler (console output) with your format
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s[%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Optional: prevent propagation to root logger to avoid duplicated logs
    logger.propagate = False
    return logger

def zip_files(output_dir, file_names, zip_name):
    with zipfile.ZipFile(f"{output_dir}/{zip_name}", 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_name in file_names:
            zipf.write(f"{output_dir}/{file_name}", arcname=os.path.basename(file_name))

# Convert dataset-column relationship to metadata feed format
def convert_dataset_column_relationship_to_metadata_feed(dataset_dataelement_relationship_master_set, resource_name, tgt_resource_name):
    table_dict = {}
    # Iterate through each (table, column) tuple
    for table, column in dataset_dataelement_relationship_master_set:
        # If the table is not yet a key in the dictionary, add it with an empty list
        table_nm_cleansed = table.replace(f"{resource_name}.", "").replace(f"{tgt_resource_name}.", "").replace("/", ".")
        if table_nm_cleansed not in table_dict:
            table_dict[table_nm_cleansed] = []
        
        # Append the column to the table's list
        col_name = column.split('/')[-1]
        if not col_name.endswith(".*"):
            # Only enrich the column metadata with real column info, 
            # table.* was a placeholder from parsing to get a column link
            # should be excluded from the metadata repo
            table_dict[table_nm_cleansed].append(col_name)
    return table_dict


# Merge multiple table dictionaries into one, this is to enrich the original
# metadata set based on last iteration information.
def merge_table_dicts(dict_list):
    merged = {}
    seen_columns_per_table = {}
    for d in dict_list:
        for table, columns in d.items():
            if table not in merged:
                merged[table] = []
                seen_columns_per_table[table] = set()
            seen = seen_columns_per_table[table]

            for col in columns:
                col_lower = col.lower()
                if col_lower not in seen:
                    merged[table].append(col)
                    seen.add(col_lower)
    return merged

# Extract metadata from YAML definition file
def extract_metadata_from_yaml(sources, default_src_db="<default>"):
    result = {}
    # Iterate over sources list
    for source in sources:
        # map table name to columns
        if 'table' in source:
            result[f"{default_src_db}.{source['table']}"] = source['columns']
        # map view name to columns (assuming same columns)
        if 'view' in source:
            result[f"{default_src_db}.{source['view']}"] = source['columns']
    return result


# Write reference assets to CSV file with default header format that can be picked up
# by Informatica CDGC custom scanner. 
# It will check if target file exist or not, if yes with header presented, it will append
# the content into the file, otherwise it will create the file with header, and then
# populate the content
def write_reference_assets_to_csv(output_dir, filepath, rows, header=['core.externalId', 'core.Reference', 'core.assignable', 'core.name']):
    """Appends reference assets to a CSV file, writes header if not present."""
    full_path = os.path.join(output_dir, filepath)
    file_exists = os.path.isfile(full_path)
    write_header = True

    if file_exists:
        # Check if header exists
        with open(full_path, 'r', newline='') as f:
            reader = csv.reader(f)
            first_row = next(reader, None)
            if first_row and [col.lower() for col in first_row] == [col.lower() for col in header]:
                write_header = False
    
    # filter rows that already exists in the file
    existing_rows = set()
    if file_exists:
        with open(full_path, 'r', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)  # Skip header
            for row in reader:
                existing_rows.add(tuple(row))
    rows = [row for row in rows if tuple(row) not in existing_rows]

    with open(full_path, 'a', newline='') as f:
        writer = csv.writer(f, delimiter=',')
        if not file_exists or write_header:
            writer.writerow(header)
        writer.writerows(rows)


# If user already provided table-column metadata, we will use that to create DataElement entries.
def update_master_tbl_col_set(tbl_col_master_set, col_master_set, src_name, tbl_name_with_schema, metadata):
    provided_columns  = metadata.get(tbl_name_with_schema, [])
    tbl_name_with_schema_formatted = tbl_name_with_schema.replace(".", "/")
    if len(provided_columns) != 0:
        # If columns are provided, use them to create DataElement entries
        tbl_id = f"{src_name}.{tbl_name_with_schema_formatted}"
        for col in provided_columns:
            col_name = str(col).strip()
            de_id = f"{tbl_id}/{col_name}"
            col_master_set.add(de_id)
            tbl_col_master_set.add((tbl_id, de_id))
    return tbl_col_master_set, col_master_set


# Compare two dictionaries ignoring the order of columns in each table
# if dict1 and dict2 are not equal, print the differences. This is for comparing
# the growth of metadata dictionary over each iteration.
def dicts_are_equal_ignore_order(dict1, dict2):
    # Check if both dictionaries have the same keys
    if dict1.keys() != dict2.keys():
        logging.info("Mismatching keys found")
        logging.debug(f"Keys only in dict1: {dict1.keys() - dict2.keys()}")
        logging.debug(f"Keys only in dict2: {dict2.keys() - dict1.keys()}")
        return False
    
    for key in dict1:
        # Convert column lists to lowercase sets to ignore order and case
        set1 = set(col.lower() for col in dict1[key])
        set2 = set(col.lower() for col in dict2[key])
        
        if set1 != set2:
            logging.debug(f"Mismatch in columns for table {key}: {set1} != {set2}")
            logging.debug(f"Diff: {set1.symmetric_difference(set2)}")
            return False

    return True