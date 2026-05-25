import os
import csv
import pandas as pd
import re
from sqllineage.runner import LineageRunner

# --- Configuration ---
INPUT_SQL_FILE = 'Test.sql'
OUTPUT_DIR = 'output'
METADATA_REPO_FILE = 'source_metadata.csv'  # Optional: file containing known table/column mapping
RESOURCE_NAME = "SQL_Lakehouse_Scanner"

class CDGCLoader:
    def __init__(self, resource_name):
        self.resource_name = resource_name
        self.resources = []
        self.datasources = []
        self.datasets = []
        self.dataelements = []
        self.links = []
        # Code Structure Assets
        self.tasks = []
        self.statements = []
        self.calculations = []
        
        # Initialize Resource
        self.resources.append({'Identity': resource_name, 'Name': resource_name, 'Class': 'core.Resource'})

    def add_asset(self, collection, identity, name, parent_id, class_type):
        if not any(d['Identity'] == identity for d in collection):
            item = {'Identity': identity, 'Name': name, 'Class': class_type}
            if parent_id:
                item['Parent Identity'] = parent_id
            collection.append(item)

    def add_link(self, from_id, to_id, assoc):
        self.links.append({'Association': assoc, 'From Object Identity': from_id, 'To Object Identity': to_id})

    def export(self, folder):
        os.makedirs(folder, exist_ok=True)
        pd.DataFrame(self.resources).to_csv(f"{folder}/core.Resource.csv", index=False)
        pd.DataFrame(self.datasources).to_csv(f"{folder}/core.DataSource.csv", index=False)
        pd.DataFrame(self.datasets).to_csv(f"{folder}/core.DataSet.csv", index=False)
        pd.DataFrame(self.dataelements).to_csv(f"{folder}/core.DataElement.csv", index=False)
        pd.DataFrame(self.links).to_csv(f"{folder}/link.csv", index=False)
        # Export logic/code assets if needed as separate files or integrated
        print(f"Metadata exported to {folder}/")

def parse_sql_recursively(sql_path, loader):
    with open(sql_path, 'r') as f:
        sql_content = f.read()

    # Initial Lineage Extraction
    runner = LineageRunner(sql_content)
    
    # Track metadata to handle 'SELECT *' and recursive discovery
    metadata_repo = {} # Format: {table_fullname: [columns]}

    # Process Tables
    for target in runner.target_tables:
        target_id = str(target)
        db_name = target.schema.parent if hasattr(target.schema, 'parent') else "DefaultDB"
        schema_name = str(target.schema)
        
        loader.add_asset(loader.datasources, db_name, db_name, loader.resource_name, 'core.DataSource')
        loader.add_asset(loader.datasets, target_id, target_id.split('.')[-1], f"{db_name}/{schema_name}", 'core.DataSet')

        # Link sources to target
        for source in runner.source_tables:
            source_id = str(source)
            loader.add_link(source_id, target_id, 'core.DataSetDataFlow')

    # Column Level Lineage (Requires sqllineage >= v1.3.x)
    try:
        column_lineage = runner.get_column_lineage()
        for path in column_lineage:
            # path is a tuple of columns (source_col, ..., target_col)
            source_col = path[0]
            target_col = path[-1]
            
            source_col_id = f"{source_col.table}.{source_col.raw_name}"
            target_col_id = f"{target_col.table}.{target_col.raw_name}"
            
            loader.add_asset(loader.dataelements, source_col_id, source_col.raw_name, str(source_col.table), 'core.DataElement')
            loader.add_asset(loader.dataelements, target_col_id, target_col.raw_name, str(target_col.table), 'core.DataElement')
            
            loader.add_link(source_col_id, target_col_id, 'core.DirectionalDataFlow')
    except Exception as e:
        print(f"Column lineage extraction note: {e}")

    # Build Logic Hierarchy: Database -> Schema -> Task -> Statement -> Calculation
    task_name = os.path.basename(sql_path)
    loader.add_asset(loader.tasks, task_name, task_name, None, 'core.Task')
    
    for i, stmt in enumerate(runner.statements):
        stmt_id = f"{task_name}/stmt_{i}"
        loader.add_asset(loader.statements, stmt_id, f"Statement_{i}", task_name, 'core.Statement')
        # Here logic for 'Calculations' would map specific column transformations

def main():
    loader = CDGCLoader(RESOURCE_NAME)
    parse_sql_recursively(INPUT_SQL_FILE, loader)
    loader.export(OUTPUT_DIR)

if __name__ == "__main__":
    main()