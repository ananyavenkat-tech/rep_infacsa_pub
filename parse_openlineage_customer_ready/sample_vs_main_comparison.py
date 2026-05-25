#!/usr/bin/env python3
"""
Compares sample input files with main input files to show dataset information completeness
"""

import json

print('='*80)
print('SAMPLE INPUT FILES VS MAIN INPUT FILES - COMPARISON')
print('='*80)

# Analyze sample file
print('\n✓ SAMPLE FILE: sample_dataset_only.ndjson')
print('-'*80)
with open('./sample_input/sample_dataset_only.ndjson', 'r') as f:
    sample = json.loads(f.readline())
    print(f'✓ Event Type: {sample.get("eventType")}')
    print(f'✓ Job Name: {sample.get("job", {}).get("name")}')
    print(f'✓ Inputs: {len(sample.get("inputs", []))} dataset(s) - {[d["name"] for d in sample.get("inputs", [])]}')
    print(f'✓ Outputs: {len(sample.get("outputs", []))} dataset(s) - {[d["name"] for d in sample.get("outputs", [])]}')
    print(f'✓ Has Schema: {bool(sample.get("inputs", [{}])[0].get("facets", {}).get("schema"))}')
    print(f'✗ Has ColumnLineage: {bool(sample.get("job", {}).get("facets", {}).get("columnLineage"))}')
    
# Analyze main input file
print('\n✗ MAIN INPUT FILE: 00b3fc34-1d8f-4a8c-b194-023a1140d5bc.ndjson')
print('-'*80)
with open('./input/00b3fc34-1d8f-4a8c-b194-023a1140d5bc.ndjson', 'r') as f:
    main = json.loads(f.readline())
    print(f'✓ Event Type: {main.get("eventType")}')
    print(f'✓ Job Name: {main.get("job", {}).get("name")}')
    print(f'✓ Inputs: {len(main.get("inputs", []))} dataset(s) - {[d["name"] for d in main.get("inputs", [])]}')
    print(f'✗ Outputs: {len(main.get("outputs", []))} dataset(s) - EMPTY!')
    print(f'✓ Has Schema: {bool(main.get("inputs", [{}])[0].get("facets", {}).get("schema"))}')
    print(f'✗ Has ColumnLineage: {bool(main.get("job", {}).get("facets", {}).get("columnLineage"))}')

print('\n' + '='*80)
print('KEY DIFFERENCES')
print('='*80)
print('Sample Files:')
print('  ✓ Source: MySQL/API/Database')
print('  ✓ Complete lineage flow (Input → Output)')
print('  ✓ Batch/ETL processing')
print('  ✓ CAN generate DataSetDataFlow')
print('')
print('Main Input Files:')
print('  ✗ Source: Kafka streaming')
print('  ✗ Incomplete lineage flow (Input only, NO Output)')
print('  ✗ STREAMING/event-based processing')
print('  ✗ CANNOT generate DataSetDataFlow (no outputs)')
print('')
print('Both missing:')
print('  ✗ columnLineage facet (cannot generate DirectionalDataFlow)')
print('='*80)
