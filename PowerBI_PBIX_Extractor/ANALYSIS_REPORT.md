# PowerBI v3 Script — Analysis & Gap Report
**Project:** Simpfy Project — Power BI vs. IDMC Assessment  
**Script:** `powerbi_v3.py`  
**Date:** 2026-05-21  

---

## 1. Overview

This document summarises the analysis performed on the `powerbi_v3.py` script, the bugs found and fixed, and a full cross-check of the script's output against the *Simpfy Project: Power BI vs. IDMC Assessment Report* PDF. It covers what is correctly captured, what is missing, and why.

---

## 2. Bugs Found and Fixed

### Bug 1 — Unguarded `match.group(1)` calls (crash on any non-column expression)

**Location:** `dataTransforms` and `prototypeQuery` branches  
**Problem:** The script used `re.search(...).group(1)` without checking whether the regex matched. DAX expression types such as `CountRows`, `HierarchyLevel`, and complex `Aggregation` nesting do not contain a `"Property"` key, causing an `AttributeError` that crashed the entire run mid-execution, leaving all CSV output files partially written and unclosed.  
**Fix:** Captured the match object first and applied a conditional fallback:
```python
match_entity = re.search(r'"Entity":\s*"([^"]+)', expr_json)
Table = match_entity.group(1) if match_entity else ""
```
For `CountRows` and other table-level measures with no `Property`, the table name is used as the column identifier so the dataset link is preserved.

---

### Bug 2 — Wrong field type written to CSV

**Location:** Both `dataTransforms` and `prototypeQuery` branches  
**Problem:** The raw DAX expression key (`"Column"`, `"Measure"`, `"Aggregation"`, etc.) was written directly into the `FieldType` column instead of the semantic type expected by IDMC (`Source Column`, `Measure`, `Calculated Column`).  
**Fix:** Added a `DAX_TYPE_MAP` dictionary and `classify_field_type()` helper:

| DAX Expression Key | Written FieldType |
|---|---|
| `Column` | `Source Column` |
| `Measure` | `Measure` |
| `Aggregation` | `Measure` |
| `CountRows` | `Measure` |
| anything else | `Calculated Column` |

---

### Bug 3 — `core.reference` / `core.assignable` column swap

**Location:** `coreResource_writer`, `coreDataSet_writer`, `coreDataElement_writer`, `coreDataSource_writer`  
**Problem:** The CSV header is `[externalId, reference, assignable, name]` but the rows were written with `'TRUE'` in the `core.reference` position instead of `core.assignable`. This would cause IDMC to ingest incorrect assignability flags.  
**Fix:** Swapped the values so all four core writers consistently write `['id', '', 'TRUE', 'name']`.

---

### Bug 4 — `dataTransforms` items without an `expr` key (crash on `queryRef` items)

**Location:** `dataTransforms` branch, line accessing `item['expr']`  
**Problem:** Some `dataTransforms['selects']` items use `queryRef` instead of `expr`. Accessing `item['expr']` unconditionally raised a `KeyError`.  
**Fix:** Added an explicit guard:
```python
if 'expr' not in item:
    print("WARN: dataTransforms select item has no 'expr' key, skipping")
    continue
```

---

### Bug 5 — `visual['config']` still a string for some visual types (TypeError crash)

**Location:** Visual loop, `'singleVisual' in visual['config']` check  
**Problem:** The global string replacement preprocessing on the raw JSON (`replace('"{', '{')` etc.) was fragile and did not guarantee all `visual['config']` values were parsed into dicts. For some visual types, `visual['config']` remained a string. The `in` operator then performed a substring search rather than a dict key lookup, and the subsequent `visual['config']['singleVisual']` subscript raised a `TypeError`.  
**Fix:** Replaced all downstream access with a safe local variable:
```python
visual_config = visual['config'] if isinstance(visual['config'], dict) else {}
```

---

### Bug 6 — JSON parse crash from fragile global string replacements

**Location:** Raw Layout file preprocessing  
**Problem:** The original preprocessing (`replace('\\','').replace('"{','{')` etc.) was a global blind replacement across the entire JSON string. At character position 233,754 of the Simpfy project Layout file, this corrupted a legitimate string value, causing `json.loads()` to fail with `Expecting ',' delimiter`.  
**Fix:** Replaced the preprocessing entirely with a recursive `decode_nested()` function that only decodes strings which are themselves valid JSON:
```python
def decode_nested(obj):
    if isinstance(obj, str):
        try: return decode_nested(json.loads(obj))
        except (json.JSONDecodeError, ValueError): return obj
    if isinstance(obj, dict): return {k: decode_nested(v) for k, v in obj.items()}
    if isinstance(obj, list): return [decode_nested(i) for i in obj]
    return obj
```
This also fixed the file handle leak — the Layout file is now opened in a `with` block so it is always closed before parsing.

---

### Bug 7 — No exception safety on main loop (CSV files left open/partial on crash)

**Location:** Main `for pbix_file in pbix_files` loop  
**Problem:** No `try/except/finally` existed. Any exception after the CSV writers had started writing would leave all 10 file handles open and the files in a partially-written, unclosed state.  
**Fix:** Wrapped the entire loop in `try/except/finally`. The `finally` block always closes all file handles regardless of success or failure. The `no pbix files` early-exit path also now closes files.

---

### Bug 8 — Dataset-level field rows written without FieldType

**Location:** `create_dataset_core()` function  
**Problem:** The function wrote dataset-level field rows (`ds_TableName/ColumnName`) with an empty `FieldType` column, because `semantic_type` was only known at the call site and not passed into the function. This resulted in 83 field rows with no type classification.  
**Fix:** Added `semantic_type=''` parameter to `create_dataset_core()` and passed it through at both call sites:
```python
def create_dataset_core(Table, Column, semantic_type=''):
    ...
    field_writer.writerow([..., semantic_type])
...
create_dataset_core(Table, Column, semantic_type)
```

---

### Bug 9 — Windows `shutil.rmtree` permission error on cleanup

**Location:** Cleanup line at end of script  
**Problem:** On Windows, files extracted from the zip into `./tmp` are sometimes read-only. `shutil.rmtree` raised `PermissionError: [WinError 32]` and `[WinError 5]`, causing the script to exit with an error even though the output zip had already been successfully created.  
**Fix:**
```python
if not DEBUG: [shutil.rmtree(dir_path, ignore_errors=True) for dir_path in ['./out', './tmp']]
```

---

## 3. Script Output — What Was Generated

After all fixes, a clean run against `Simpfy project.pbix` produced `output/powerbi.zip` containing:

| File | Rows | Description |
|---|---|---|
| `custom.PowerBI.v3.Report.csv` | 1 | Report entry |
| `custom.PowerBI.v3.Section.csv` | 26 | All report pages |
| `custom.PowerBI.v3.Visual.csv` | 181 | All visuals across all pages |
| `custom.PowerBI.v3.Field.csv` | 424 | All fields with correct FieldType |
| `custom.PowerBI.v3.Dataset.csv` | 6 | PBIX-side dataset wrappers |
| `core.Resource.csv` | 6 | IDMC core resource entries |
| `core.DataSource.csv` | 6 | IDMC core data source entries |
| `core.DataSet.csv` | 6 | IDMC core dataset entries |
| `core.DataElement.csv` | 83 | IDMC core data element entries |
| `links.csv` | 1,361 | All lineage relationships |

**Field type breakdown across all captured tables:**

| Table | Source Columns | Measures | Total |
|---|---|---|---|
| `simpfy_projects_vw` | 38 | 23 | 61 |
| `simpfy_challenges_vw` | 15 | 3 | 18 |
| `simpfy_kpi_value` | 1 | 0 | 1 |
| `LastMilestonesTable` | 1 | 0 | 1 |
| `Stage_sort` | 1 | 0 | 1 |
| `Unique_Projects` | 1 | 0 | 1 |

---

## 4. Cross-Check Against PDF Assessment Report

### Section 2 — Table Coverage

| PDF Table | Script Status | Reason if Missing |
|---|---|---|
| `Bridge over challenge id` | **MISSING** | Model-only hardcoded lookup — no visual references it |
| `Bridge over segments` | **MISSING** | Model-only bridge table — no visual references it |
| `LastMilestonesTable` | CAPTURED (1 field) | DAX-computed at runtime — only 1 field visible in Layout |
| `Quarter table` | **MISSING** | Model-only — slicers use it via relationships, not directly |
| `simpfy_challenges_vw` | **CAPTURED** | 18 fields (15 Source Columns, 3 Measures) |
| `simpfy_kpi_value` | **CAPTURED** | 1 field |
| `simpfy_projects_vw` | **CAPTURED** | 61 fields (38 Source Columns, 23 Measures) |

---

### Section 3 — Gap Log Fields

| Table | Field | Expected Type | Script Output | Status | Reason |
|---|---|---|---|---|---|
| `simpfy_projects_vw` | `2024 YEAR` | Measure | — | **MISSING** | DAX measure with hardcoded year value; no visual references it |
| `simpfy_projects_vw` | `AVG total cost` | Measure | — | **MISSING** | DAX measure not placed on any report page |
| `simpfy_projects_vw` | `business_unit_id` | Source Column | — | **MISSING** | Relationship key column; never displayed in any visual |
| `simpfy_projects_vw` | `capex by stage` | Measure | — | **MISSING** | DAX measure not placed on any report page |
| `simpfy_challenges_vw` | `segment_type` | Source Column | Source Column | **CORRECT** | Correctly captured |
| `simpfy_challenges_vw` | `Status` | Calculated Column | Source Column | **PARTIAL** | Layout stores it as `Column` — cannot distinguish Source vs Calculated without DataModelSchema |
| `simpfy_kpi_value` | `kpivalue` | Source Column | — | **MISSING** | Column name in the model; visuals reference it by a display alias (`Name`) not `kpivalue` |
| `LastMilestonesTable` | `LastMilestoneDate` | Source Column | — | **MISSING** | DAX-computed table — only `LastMilestoneStatus` visible in Layout visuals |
| `Stage_sort` | `Sort` | Source Column | — | **MISSING** | Model-only sort column used via "Sort by Column" — never placed on any visual |

---

### Section 4 — Report Page & Visual Content Mapping

| Page | Visual Type | Field | Section Captured | Visual Captured | Field Captured |
|---|---|---|---|---|---|
| Executive Summary | Slicer | Pillars | Yes | Yes | Yes |
| Executive Summary | Slicer | Enablers | Yes | Yes | Yes |
| Executive Summary | KPI | distinct_count_pillars | Yes | Yes | Yes |
| Key Challenges | Donut Chart | challenge_impact_level | Yes | Yes | Yes |
| Key Challenges | Table | segment_name | Yes | Yes | Yes |
| Key Challenges | Table | challenge title (area of concern) | Yes | Yes | Yes |

**Section 4: 100% coverage — all 6 page/visual/field mappings fully captured.**

---

## 5. Remaining Gaps and Root Causes

All remaining gaps fall into one of three categories, all of which require the `DataModelSchema` to resolve:

### Category A — Model-only tables (no visual reference)
`Bridge over challenge id`, `Bridge over segments`, `Quarter table`  
These tables exist only in the data model and are not directly referenced by any visual on any report page. The script discovers tables by walking the Layout file's visual expressions — tables that only participate via model relationships are invisible to it.

### Category B — DAX measures / columns not placed on any page
`2024 YEAR`, `AVG total cost`, `capex by stage`, `kpivalue`, `LastMilestoneDate`  
These are defined in the data model (DAX measures or calculated columns) but no report page visual directly uses them. The Layout file only records what visuals actually display.

### Category C — Relationship key columns
`business_unit_id`  
Used internally as a join key between tables. Never displayed in any visual. Invisible to Layout-only parsing.

### Category D — Source Column vs Calculated Column ambiguity
`Status` (on `simpfy_challenges_vw`)  
The Layout file uses expression type `"Column"` for both regular source columns and DAX calculated columns. The distinction is only stored in the DataModel. Without DataModelSchema, the script correctly falls back to `Source Column`.

### Category E — Model-only sort columns
`Stage_sort.Sort`  
This is a classic Power BI pattern: a numeric `Sort` column is added to the model and assigned via *"Sort by Column"* to order another column (`Stage`) in visuals. The `Sort` column itself is never placed on any visual — it operates purely as an internal sort key inside the data model. As a result it has no entry in the Layout file and is invisible to layout-only parsing.

**Confirmed in Layout:** Only `Stage_sort.Stage` appears (used in the Executive Summary pie chart). `Stage_sort.Sort` has zero visual references across all 26 pages. This field can only be captured via DataModelSchema.

---

## 6. How to Resolve Remaining Gaps

To capture the missing tables and fields, the DataModelSchema is required. The two practical options are:

**Option 1 — Export as PBIT (Power BI Template)**  
In Power BI Desktop: *File → Export → Power BI Template*. The resulting `.pbit` file is a zip containing a `DataModelSchema` file in plain JSON. Drop the `.pbit` into the `input/` folder and the script can be extended to also read it.

**Option 2 — pbi-tools (open source CLI)**  
Run `pbi-tools extract "Simpfy project.pbix"`. This produces a `Model/database.json` file containing the full table and column definitions including calculated columns, measures, and relationship keys.

> **Note:** The `DataModel` file inside the PBIX is XPress9 compressed binary (Microsoft Vertipaq engine format) and cannot be read with standard Python libraries.

---

## 7. Summary

| Category | Total | Fixed/Captured | Remaining |
|---|---|---|---|
| Script bugs fixed | 9 | 9 | 0 |
| PDF tables captured | 7 | 4 | 3 (model-only) |
| PDF gap log fields | 9 | 1 correct, 1 partial | 7 (model-only/DAX/sort) |
| PDF visual mappings | 6 | 6 | 0 |
| Report sections | 26 | 26 | 0 |
| Report visuals | 181 | 181 | 0 |
| Lineage links | 1,361 | 1,361 | 0 |

The script correctly extracts everything available from the Layout file. All remaining gaps require reading the DataModelSchema, which is not accessible from the PBIX binary without an external tool (PBIT export or pbi-tools).
