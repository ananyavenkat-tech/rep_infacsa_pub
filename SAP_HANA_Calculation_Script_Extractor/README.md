# HANA Calculation View → CDGC Metadata Import

Parses SAP HANA Calculation View files (`.hdbcalculationview`) and generates
a CDGC-compatible ZIP of CSV files ready for custom scanner import.

---

## Project Structure

```
hana_cdgc_project/
├── Model/
│   └── custom_saphana_calculationscript_v7.json   ← Step 1: register in CDGC
├── input/
│   └── *.hdbcalculationview                        ← place source files here
├── output/
│   └── hana_cdgc_import_v7.zip                    ← Step 2: import into CDGC
├── output_unzip/
│   └── *.csv                                       ← extracted CSVs (for review)
├── scripts/
│   └── hana_calcview_to_cdgc_csv_v7.py            ← main parser script
└── README.md
```

---

## Usage

```bash
# Default: reads input/, writes output/hana_cdgc_import_v7.zip
python scripts/hana_calcview_to_cdgc_csv_v7.py

# Specific input directory
python scripts/hana_calcview_to_cdgc_csv_v7.py path/to/dir

# Single file
python scripts/hana_calcview_to_cdgc_csv_v7.py path/to/file.hdbcalculationview

# Environment variable override
HANA_SCRIPTS_DIR=./input python scripts/hana_calcview_to_cdgc_csv_v7.py
```

No third-party dependencies — uses Python standard library only (`csv`, `re`,
`xml.etree`, `zipfile`). Requires Python 3.8+.

---

## Sample Input Views

| File | View ID | CTEs | Source Tables | Output Fields |
|------|---------|------|---------------|---------------|
| `CV_AR_AGING.hdbcalculationview` | `CV_AR_AGING` | `open_items`, `enriched` | BSID, KNA1 | 11 |
| `CV_INVENTORY_STOCK.hdbcalculationview` | `CV_INVENTORY_STOCK` | `stock_base`, `material_info` | MARD, MARA, MAKT, T001W, MBEW | 11 |
| `CV_SALES_ORDERS.hdbcalculationview` | `CV_SALES_ORDERS` | `order_header`, `order_items`, `enriched` | VBAK, VBAP, KNA1 | 13 |
| `TI_VENDOR_5Y.hdbcalculationview` | `TI_VENDOR_5Y` | `BS`, `cte2`, `cte3` | BSEG, FAGLFLEXA, T012K, BKPF, FEBCL, DIM_VEND, DIM_CUST | 25 |

---

## Metamodel Architecture

### Reference objects → native CDGC core classes

Base physical objects are mapped to core CDGC classes with `core.Reference = TRUE`
so CDGC resolves them against existing catalog entries rather than creating duplicates.

| HANA Object     | CDGC Class         | Reference |
|-----------------|--------------------|-----------|
| Connection root | `core.Resource`    | TRUE      |
| Schema / DB     | `core.DataSource`  | TRUE      |
| Physical table  | `core.DataSet`     | TRUE      |
| Table column    | `core.DataElement` | TRUE      |

### Custom metamodel → `custom.sap.hana.calscript.v7`

Internal Calculation View execution structures use the registered custom model.

| Object                  | Custom Class                                     | Extends          |
|-------------------------|--------------------------------------------------|------------------|
| Calc View output        | `custom.sap.hana.calscript.v7.HanaCalcView`      | `core.DataSet`   |
| Output column           | `custom.sap.hana.calscript.v7.HanaCalcViewField` | `core.DataElement` |
| SQL CTE / internal node | `custom.sap.hana.calscript.v7.HanaScriptBlock`   | `core.DataSet`   |

---

## CSV Files Produced

| File | Class | Key Fields | Description |
|------|-------|------------|-------------|
| `core.Resource.csv` | `core.Resource` | `core.externalId`, `core.name`, `core.reference` | One synthetic connection node per unique HANA schema |
| `core.DataSource.csv` | `core.DataSource` | `core.externalId`, `core.Reference`, `core.name` | One row per schema + one synthetic `hana_calcviews_container` |
| `core.DataSet.csv` | `core.DataSet` | `core.externalId`, `core.Reference`, `core.name` | One row per physical table referenced in SQL |
| `core.DataElement.csv` | `core.DataElement` | `core.externalId`, `core.Reference`, `core.name` | Physical columns extracted via SQL alias parsing |
| `custom…HanaCalcView.csv` | `HanaCalcView` | `core.externalId`, `packagePath`, `calcViewType`, `defaultClient` | One row per `.hdbcalculationview` file |
| `custom…HanaCalcViewField.csv` | `HanaCalcViewField` | `core.externalId`, `core.name`, `columnDataType`, `keyAttribute` | Output attributes and measures from `<logicalModel>` |
| `custom…HanaScriptBlock.csv` | `HanaScriptBlock` | `core.externalId`, `core.name`, `core.parent`, `scriptType`, `transformationLogic` | One row per SQL CTE; `core.parent` = owning HanaCalcView |
| `links.csv` | — | `Source`, `Target`, `Association` | All structural hierarchy and lineage relationships |

---

## Lineage Flow

Object-level lineage uses `core.DataSetDataFlow`. Each physical table is wired
only to the CTE that directly references it — not blindly to the first CTE in
the chain (see **Scoped CTE Dependency Analysis** below).

```
$RESOURCE
  └── REF_HANA_<SCHEMA>_CONN          (core.Resource)
        └── REF_HANA_<SCHEMA>_DS      (core.DataSource)
              └── TABLE_A             (core.DataSet)
              │     └── COL_1         (core.DataElement)
              └── TABLE_B             (core.DataSet)
                    └── COL_2         (core.DataElement)

  └── hana_calcviews_container        (core.DataSource, synthetic)
        └── HanaCalcView              ◄── DataSetDataFlow from last CTE
              ├── HanaCalcViewField   ◄── DirectionalDataFlow from source DataElement
              └── HanaScriptBlock [CTE_1]   ◄── DataSetDataFlow from TABLE_A only
                    │  core.DataSetDataFlow
                    ▼
              HanaScriptBlock [CTE_2]  ◄── DataSetDataFlow from TABLE_B + CTE_1
                    │  core.DataSetDataFlow
                    ▼
              HanaCalcView
```

**Example — CV_INVENTORY_STOCK (branched topology):**

```
MARD  ──────────────────────────► stock_base
                                        │
MARA ──────────────────────────┐        │ (prior CTE)
MAKT ──────────────────────────┼──► material_info ──► CV_INVENTORY_STOCK
T001W ─────────────────────────┤
MBEW ──────────────────────────┘
```

MARA/MAKT/T001W/MBEW feed `material_info` directly (they appear in its JOIN
clauses), not `stock_base`. The scoped parser detects this correctly.

### Link Associations Used

| Association | Purpose |
|---|---|
| `core.ResourceParentChild` | `$RESOURCE → Resource`, `Resource → DataSource` |
| `core.DataSourceParentChild` | `DataSource → DataSet`, `container → HanaCalcView` |
| `core.DataSetToDataElementParentship` | `DataSet → DataElement` |
| `core.DataSetDataFlow` | Object-level lineage: tables → CTEs → HanaCalcView |
| `core.DirectionalDataFlow` | Column-level lineage: `DataElement → HanaCalcViewField` |
| `custom…HanaCalcViewToHanaCalcViewField` | ParentChild: view owns its output fields |
| `custom…HanaCalcViewToHanaScriptBlock` | ParentChild: view owns its CTE blocks |

---

## Scoped CTE Dependency Analysis

Earlier versions ran a global regex across the entire SQL body, which caused a
critical lineage error: all source tables were incorrectly attributed to the
**first** CTE in the WITH chain, regardless of which CTE actually referenced them.

The parser now uses a two-pass approach for every script node:

1. **`_extract_cte_body_balanced(sql, cte_name)`** — isolates each CTE's body
   text using balanced-parenthesis depth counting. This handles nested
   sub-selects and CASE/WHEN expressions that contain their own `( )` pairs,
   which a regex cannot reliably handle.

2. **`_build_cte_dependency_map(sql, cte_names, data_sources)`** — for each
   CTE, searches *only that CTE's isolated body* to find:
   - Physical tables directly referenced (via `"SCHEMA"."TABLE"` patterns)
   - Prior CTE names used as inputs (word-boundary match against earlier CTEs)

   Returns `{cte_name: {"tables": [...], "prior_ctes": [...]}}`.

`build_links` then emits `core.DataSetDataFlow` edges per CTE using this map,
so each table edge points to exactly the CTE that owns it.

---

## Column Extraction Logic

Physical table columns are extracted from the SQL body by:

1. Building an **alias map** from `FROM/JOIN "SCHEMA"."TABLE" [AS] alias` patterns
2. Scanning all `alias.column` references in the SQL
3. Filtering out SQL keywords, CTE names, and function names
4. Resolving each alias back to its `(schema, table)` to produce the full
   `REF_HANA_<SCHEMA>_DS/<TABLE>/<COLUMN>` external ID

Column-level lineage (`core.DirectionalDataFlow`) then maps each source
`DataElement` directly to the matching `HanaCalcViewField` by name.

---

## CDGC Import Steps

1. **Register the model** — upload
   `Model/custom_saphana_calculationscript_v7.json` to CDGC via
   *Metadata Command Center → Custom Model Management* before importing any CSVs.

2. **Run the parser** — execute the script to regenerate the ZIP from the
   latest input files.

3. **Import the ZIP** — upload `output/hana_cdgc_import_v7.zip` via
   *Metadata Command Center → Custom Scanner Import*.

4. **Review in output_unzip/** — the same CSVs are extracted here for local
   inspection before import.
