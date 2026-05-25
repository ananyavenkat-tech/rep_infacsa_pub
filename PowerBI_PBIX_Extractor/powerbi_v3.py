"""
Script Name: powerbi_v3.py
Description: 
    This script converts powerbi on-prem pbix file into Informatica CDGC custom scanner payload for lineage 
Safe Harbor: 
    This is a prototype with no official Informatica GCS support.
    use of this script is collected (time, script name, hostname) for statistics (see below to opt-out) 
Usage:
    - import & publish in MCC the model "custom.PowerBI.v3"
    - place pbix file into input folder and run script
    - create/run a custom scanner in MCC using the payload created in output folder
Note:
    - set DEBUG=True if you want to debug (output contains csv files, tmp/Report contains the payload)
    - set STATS=False if you don't want to share usage statistics 
"""
# =============================================================================
# BUSINESS CONTEXT
# -----------------------------------------------------------------------------
# Converts Power BI .pbix files into an Informatica CDGC custom scanner
# metadata import package (ZIP of CSVs) for lineage and cataloging.
#
# Flow:
#   1. Reads .pbix files from ./input/ (each is a ZIP containing Report/Layout)
#   2. Parses the report hierarchy: Report → Sections → Visuals → Fields
#   3. Resolves each visual field back to its source Table.Column via DAX
#      expression parsing (handles Columns, Measures, and Aggregations)
#   4. Writes 9 CDGC-compatible CSVs:
#        Custom model : Report, Section, Visual, Dataset, Field
#        Core CDGC    : core.Resource, core.DataSource, core.DataSet,
#                       core.DataElement, links.csv
#   5. Packages output into ./output/powerbi.zip ready for upload to
#      Informatica CDGC via Metadata Command Center → Custom Scanner Import
# =============================================================================
#-- edit parameters [True|False] for debugging and statistics collection
DEBUG=False
STATS=True
#-- Power BI internal table (not an external source reference) — core.reference='' for this table and its children
PBIX_OWNED_TABLE="Unique_Projects"
#-- display name used for the PBIX-owned resource in IDMC (the container for all Power BI objects)
PBIX_OWNED_RESOURCE_NAME="PowerBI_Projects"
#-- do not edit below (or at your own risk)
import os, time, socket, json, requests, zipfile, csv, json, shutil, re, hashlib
model="custom.PowerBI.v3"
#-- create output & temp folders
if not os.path.exists('./tmp'): os.mkdir('./tmp')
if not os.path.exists('./out'): os.mkdir('./out')
if not os.path.exists('./output'): os.mkdir('./output')
#-- create custom model csv payloads
reportFile = open(f'./out/{model}.Report.csv', 'w', encoding='utf8', newline='')
report_writer = csv.writer(reportFile)
report_writer.writerow(['core.externalId','core.name','core.description','core.businessDescription','core.businessName','core.reference'])
sectionFile = open(f'./out/{model}.Section.csv', 'w', encoding='utf8', newline='')
section_writer = csv.writer(sectionFile)
section_writer.writerow(['core.externalId','core.name','core.description','core.businessDescription','core.businessName','core.reference'])
visualFile = open(f'./out/{model}.Visual.csv', 'w', encoding='utf8', newline='')
visual_writer = csv.writer(visualFile)
visual_writer.writerow(['core.externalId','core.name','core.description','core.businessDescription','core.businessName','core.reference',f'{model}.VisualQuery',f'{model}.VisualType'])
datasetFile = open(f'./out/{model}.Dataset.csv', 'w', encoding='utf8', newline='')
dataset_writer = csv.writer(datasetFile)
dataset_writer.writerow(['core.externalId','core.name','core.description','core.businessDescription','core.businessName','core.reference'])
fieldFile = open(f'./out/{model}.Field.csv', 'w', encoding='utf8', newline='')
field_writer = csv.writer(fieldFile)
field_writer.writerow(['core.externalId','core.name','core.description','core.businessDescription','core.businessName','core.reference',f'{model}.FieldType'])
coreResourceFile = open(f'./out/core.Resource.csv', 'w', encoding='utf8', newline='')
coreResource_writer = csv.writer(coreResourceFile)
coreResource_writer.writerow(['core.externalId','core.reference','core.assignable','core.name'])
coreDataSourceFile = open('./out/core.DataSource.csv', 'w', encoding='utf8', newline='')
coreDataSource_writer = csv.writer(coreDataSourceFile)
coreDataSource_writer.writerow(['core.externalId','core.reference','core.assignable','core.name'])
coreDataSetFile = open('./out/core.DataSet.csv', 'w', encoding='utf8', newline='')
coreDataSet_writer = csv.writer(coreDataSetFile)
coreDataSet_writer.writerow(['core.externalId','core.reference','core.assignable','core.name'])
coreDataElementFile = open('./out/core.DataElement.csv', 'w', encoding='utf8', newline='')
coreDataElement_writer = csv.writer(coreDataElementFile)
coreDataElement_writer.writerow(['core.externalId','core.reference','core.assignable','core.name'])
linkFile = open('./out/links.csv', 'w', encoding='utf8', newline='')
link_writer = csv.writer(linkFile)
link_writer.writerow(['Source','Target','Association'])
#-- dedup lists are reset per PBIX file inside the loop below

DAX_TYPE_MAP = {
    'Column':     'Source Column',
    'Measure':    'Measure',
    'Aggregation':'Measure',
    'CountRows':  'Measure',
}

def classify_field_type(dax_type):
    return DAX_TYPE_MAP.get(dax_type, 'Calculated Column')

def sid(s):
    return re.sub(r'[^A-Za-z0-9_\-]', '_', s).strip('_-')

def extract_entity_property(expr_obj):
    """Recursively extract (Entity, Property) from any DAX expression dict.
    Handles Column/Measure (direct SourceRef) and Aggregation (nested Column expr).
    """
    if not isinstance(expr_obj, dict):
        return '', ''
    src_ref = (expr_obj.get('Expression') or {}).get('SourceRef', {})
    if src_ref.get('Entity'):
        return src_ref['Entity'], expr_obj.get('Property', '')
    # Aggregation: SourceRef is nested inside inner expression (e.g. Column inside Aggregation)
    inner = expr_obj.get('Expression') or {}
    for val in inner.values():
        t, c = extract_entity_property(val)
        if t:
            return t, c
    return '', ''

def extract_source_property(expr_obj):
    """Same as extract_entity_property but returns Source alias (for prototypeQuery branch)."""
    if not isinstance(expr_obj, dict):
        return '', ''
    src_ref = (expr_obj.get('Expression') or {}).get('SourceRef', {})
    if src_ref.get('Source'):
        return src_ref['Source'], expr_obj.get('Property', '')
    inner = expr_obj.get('Expression') or {}
    for val in inner.values():
        s, c = extract_source_property(val)
        if s:
            return s, c
    return '', ''

def create_dataset_core(Table, Column, semantic_type=''):
    dataset_name=report_uid+'/ds_'+sid(Table)
    dataset_id=dataset_name
    if dataset_name not in datasetList:
        datasetList.append(dataset_name)
        dataset_writer.writerow([dataset_id,'ds_'+Table,'ds_'+Table,'ds_'+Table,'ds_'+Table,''])
        link_writer.writerow([report_id,dataset_id,f'{model}.ReportToDataset'])
    datasetlinkname=dataset_id+visual_id
    if datasetlinkname not in datasetLinkList:
        datasetLinkList.append(datasetlinkname)
        link_writer.writerow([dataset_id,visual_id,'core.DataSetDataFlow'])
    datasetfield_name=Column
    datasetfield_id=dataset_name+"/"+sid(datasetfield_name)
    if datasetfield_id not in datasetFieldList:
        datasetFieldList.append(datasetfield_id)
        field_writer.writerow([datasetfield_id,datasetfield_name,datasetfield_name,datasetfield_name,datasetfield_name,'',semantic_type])
        link_writer.writerow([dataset_id,datasetfield_id,f'{model}.DatasetToField'])
    link_writer.writerow([datasetfield_id,field_id,'core.DirectionalDataFlow'])
    is_reference = 'TRUE'
    #-- write core Resource & link if not exist
    coreResource_name="Reference_"+Table
    coreResource_id=report_uid+"/Reference_"+sid(Table)
    coreResource_display=PBIX_OWNED_RESOURCE_NAME if Table == PBIX_OWNED_TABLE else coreResource_name
    if coreResource_id not in coreResourceList:
        coreResourceList.append(coreResource_id)
        coreResource_writer.writerow([coreResource_id,is_reference,'TRUE',coreResource_display])
        link_writer.writerow(['$resource',coreResource_id,'core.ResourceParentChild'])
    #-- write core Data Source & link if not exist
    coreDataSource_name="Connection_"+Table
    coreDataSource_id=report_uid+"/Connection_"+sid(Table)
    if coreDataSource_id not in coreDataSourceList:
        coreDataSourceList.append(coreDataSource_id)
        coreDataSource_writer.writerow([coreDataSource_id,is_reference,'TRUE',coreDataSource_name])
        link_writer.writerow([coreResource_id,coreDataSource_id,'core.ResourceParentChild'])
    #-- write core Data Set & link if not exist
    coreDataSet_name=Table
    coreDataSet_id=report_uid+"/"+sid(Table)
    if coreDataSet_id not in coreDataSetList:
        coreDataSetList.append(coreDataSet_id)
        coreDataSet_writer.writerow([coreDataSet_id,is_reference,'TRUE',coreDataSet_name])
        link_writer.writerow([coreDataSource_id,coreDataSet_id,'core.DataSourceParentChild'])
        link_writer.writerow([coreDataSet_id,dataset_id,'core.DataSetDataFlow'])
    #-- write core Data Element
    coreDataElement_name=Column
    coreDataElement_id=report_uid+"/"+sid(Table)+"/"+sid(Column)
    if coreDataElement_id not in coreDataSetFieldList:
        coreDataSetFieldList.append(coreDataElement_id)
        coreDataElement_writer.writerow([coreDataElement_id,is_reference,'TRUE',coreDataElement_name])
        link_writer.writerow([coreDataSet_id,coreDataElement_id,'core.DataSetToDataElementParentship'])
        link_writer.writerow([coreDataElement_id,datasetfield_id,'core.DirectionalDataFlow']) 

all_files = [reportFile, sectionFile, visualFile, datasetFile, fieldFile, linkFile, coreResourceFile, coreDataSourceFile, coreDataSetFile, coreDataElementFile]

#-- extract report (pbix file name) , sections, visuals, fields datasets & core resources
pbix_files = [file for file in os.listdir('./input') if file.endswith('.pbix')]
if not pbix_files:
    print("Alert: No '.pbix' files found in input directory!")
    [file_object.close() for file_object in all_files]
else:
  try:
    for pbix_file in pbix_files:
        report_name = os.path.splitext(pbix_file)[0]
        #-- generate stable unique prefix from filename hash (first 8 hex chars of MD5)
        #-- filename-based so re-uploading the same file always produces the same externalId
        #-- preserving any IDMC connection assignments made after previous runs
        file_hash = hashlib.md5(report_name.encode()).hexdigest()[:8]
        report_uid = f"{sid(report_name)}_{file_hash}"
        #-- reset dedup lists for each PBIX so tables from different files don't suppress each other
        datasetList=[]
        datasetLinkList=[]
        datasetFieldList=[]
        coreResourceList=[]
        coreDataSourceList=[]
        coreDataSetList=[]
        coreDataSetFieldList=[] 
        #-- check Report/Layout exists and extract it from zip format    
        with zipfile.ZipFile('./input/'+pbix_file, 'r') as zip_ref: 
            layout_path="" 
            for file_info in zip_ref.infolist():
                if file_info.filename.endswith('Report/Layout'):
                    zip_ref.extract(file_info, f'./tmp/{report_name}')
                    layout_path=f'./tmp/{report_name}/{file_info.filename}'
            if layout_path=="" :
                print(f"ERROR {pbix_file} contains no Report/Layout file")
                continue
            #-- extract report (pbix file name) , sections, visuals, fields datasets & core resources
            with open(f'{layout_path}', 'r', encoding='utf-16-le') as file:
                raw = file.read()
            # recursively decode nested JSON-encoded strings instead of fragile global replacements
            def decode_nested(obj):
                if isinstance(obj, str):
                    try: return decode_nested(json.loads(obj))
                    except (json.JSONDecodeError, ValueError): return obj
                if isinstance(obj, dict): return {k: decode_nested(v) for k, v in obj.items()}
                if isinstance(obj, list): return [decode_nested(i) for i in obj]
                return obj
            data = decode_nested(json.loads(raw))
            # -- write report layout as json content
            with open(f'{layout_path}.json', 'w', encoding='utf-16') as json_file: json.dump(data, json_file, ensure_ascii=False, indent=2)
            json_file.close()
            # -- extract report content
            print("INFO: Extracting report from file: "+pbix_file)
            report_id=report_uid
            report_writer.writerow([report_id,report_name,report_name,report_name,report_name,''])
            link_writer.writerow(['$resource',report_id,'core.ResourceParentChild'])    
            # -- extract sections content
            print("INFO: Extracting sections from report: "+report_name)
            for section in data['sections'] : 
                # -- write section output content
                section_name=section['displayName']
                section_id=report_id+"/"+section['name']
                section_writer.writerow([section_id,section_name,section_name,section_name,section_name,''])
                link_writer.writerow([report_id,section_id,f'{model}.ReportToSection'])
                print("INFO: Extracting visuals from section: "+section_name)
                visual_nb=1
                for visual in section['visualContainers'] :
                    # -- ensure visual config is a dict (may still be a raw string for some visual types)
                    visual_config = visual['config'] if isinstance(visual['config'], dict) else {}
                    # -- write Visual output content
                    if 'singleVisual' in visual_config and 'prototypeQuery' in visual_config['singleVisual']:              
                        visual_type=visual_config['singleVisual']['visualType']
                        visual_query=visual_config['singleVisual']['prototypeQuery'] 
                        visual_name="Visual"+str(visual_nb)+"_"+visual_type
                        visual_id=section_id+'/'+visual_name
                        visual_writer.writerow([visual_id,visual_name,visual_name,visual_name,visual_name,'',visual_query,visual_type])
                        link_writer.writerow([section_id,visual_id,f'{model}.SectionToVisual'])                
                        visual_nb+=1
                        # -- write field & dataset output content from dataTransforms or prototypeQuery
                        print("INFO: Extracting fields from visual: "+visual_name)
                        Table=""
                        Column=""
                        if "dataTransforms" in visual :
                            for item in visual['dataTransforms']['selects'] :
                                if 'expr' not in item:
                                    print(f"WARN: dataTransforms select item has no 'expr' key (queryRef?), skipping")
                                    continue
                                field_type=next(iter(item['expr']))
                                expr_obj=item['expr'][field_type]
                                # navigate the decoded dict directly — handles Column, Measure, Aggregation nesting
                                Table, Column = extract_entity_property(expr_obj)
                                if not Column: Column = Table
                                # -- write Field output content
                                field_name=(item.get('displayName','') or '').strip() or (item.get('queryName','') or '').strip()
                                field_desc=item.get('queryName','')
                                if not field_name:
                                    print(f"WARN: field with no displayName or queryName, skipping")
                                    continue
                                # use queryName as id suffix to avoid collisions when two fields share a displayName
                                field_id=visual_id+'/'+sid(item.get('queryName','') or field_name)
                                semantic_type=classify_field_type(field_type)
                                field_writer.writerow([field_id,field_name,field_desc,field_name,field_name,'', semantic_type])
                                link_writer.writerow([visual_id,field_id,f'{model}.VisualToField'])
                                # -- write Dataset output content (if detected)
                                if Table != "" and Column !="": create_dataset_core(Table, Column, semantic_type)
                        elif 'prototypeQuery' in visual_config['singleVisual']:
                            #-- extract dictionary Table from prototypeQuery
                            dictTables = { table['Name']: table['Entity'] for table in visual_config['singleVisual']['prototypeQuery']['From'] }
                            for item in visual_config['singleVisual']['prototypeQuery']['Select'] :
                                field_type=next(iter(item))
                                expr_obj=item[field_type]
                                # navigate the decoded dict directly — handles Column, Measure, Aggregation nesting
                                table_id, Column = extract_source_property(expr_obj)
                                if not table_id:
                                    print(f"WARN: no Source found in {field_type} expr, skipping field")
                                    continue
                                Table  = dictTables.get(table_id, '')
                                if not Column: Column = Table
                                # -- write Field output content
                                field_name=item.get('Name','')
                                if not field_name:
                                    print(f"WARN: prototypeQuery Select item has no Name, skipping")
                                    continue
                                field_id=visual_id+'/'+sid(field_name)
                                semantic_type=classify_field_type(field_type)
                                field_writer.writerow([field_id,field_name,field_name,field_name,field_name,'', semantic_type])
                                link_writer.writerow([visual_id,field_id,f'{model}.VisualToField'])
                                # -- write Dataset output content (if detected)
                                if Table != "" and Column !="": create_dataset_core(Table, Column, semantic_type)
  except Exception as e:
    print(f"ERROR: Processing failed — {e}")
  finally:
    [file_object.close() for file_object in all_files]

#-- Create output zip file
shutil.make_archive(f'./output/powerbi', 'zip', './out')
#-- Copy CSVs into unzipped output folder alongside the zip
unzip_dir='./output/powerbi'
if not os.path.exists(unzip_dir): os.mkdir(unzip_dir)
for csv_file in os.listdir('./out'):
    shutil.copy(f'./out/{csv_file}', f'{unzip_dir}/{csv_file}')
print(f"INFO: CSV files also saved to {unzip_dir}/")
#-- Cleanse  tmp & out folders (set DEBUG=True for debug)
if not DEBUG: [shutil.rmtree(dir_path, ignore_errors=True) for dir_path in ['./out', './tmp']]
#-- track usage for statistis  (set STATS=False to opt-out)
if STATS: 
    try: response=requests.post("https://infa-lic-worker.tim-qin-yujue.workers.dev", data=json.dumps({"logs": [{"timestamp": time.time(), "function": f"[{os.path.basename(__file__)}][main]", "execution_time": "", "annotation": model, "machine": socket.gethostname()}]}), headers={"Content-Type": "application/json", "X-Auth-Key": "b74a58ca9f170e49f65b7c56df0f452b0861c8c870864599b2fbc656ff758f5d"})
    except: quit()