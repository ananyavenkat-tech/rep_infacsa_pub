# PowerBI v3 — Custom Scanner for Informatica CDGC

## Overview

`powerbi_v3.py` converts Power BI `.pbix` files into Informatica CDGC custom scanner CSV payloads for lineage. It reads the Layout file inside each PBIX, extracts reports, sections, visuals, fields, datasets, and core catalog references, then writes them into a `output/powerbi.zip` ready for upload.

---

## How to Use

1. Publish the model `custom.PowerBI.v3` in MCC before the first run.
2. Place one or more `.pbix` files in the `input/` folder.
3. Run the script:
   ```
   python powerbi_v3.py
   ```
4. Upload `output/powerbi.zip` via a Custom Scanner job in MCC.

---

## External ID — How It Is Built

Every object written to the CSV payload has a `core.externalId` that uniquely identifies it in IDMC. The format is:

```
<ReportUID>/<path/to/object>
```

Where `ReportUID` is:

```
<filename_without_extension>_<hash>
```

The `<hash>` is the **first 8 characters of the MD5 hash of the report filename** (without extension). Spaces in filenames are replaced with underscores.

### Example

| PBIX filename | Report UID |
|---|---|
| `Simpfy project.pbix` | `Simpfy_project_96010eb4` |
| `Sample Sales Report.pbix` | `Sample_Sales_Report_78752638` |

Full externalId examples:

```
Simpfy_project_96010eb4
Simpfy_project_96010eb4/Executive_Summary
Simpfy_project_96010eb4/Executive_Summary/Visual1_card
Simpfy_project_96010eb4/Reference_simpfy_projects_vw
Simpfy_project_96010eb4/simpfy_projects_vw
Simpfy_project_96010eb4/simpfy_projects_vw/project_name
```

### Why a Hash?

- The hash makes each report's externalIds **globally unique** even when two reports from different teams happen to have the same filename.
- The hash is derived only from the **filename**, not from file content or a timestamp, so it is **stable across re-uploads**. Re-running the script on the same file always produces the identical externalId.
- This means any **connection assignments** made in IDMC after the first upload (linking `Reference_*` resources to their actual catalog sources) are **preserved on every subsequent run** — they will not be lost or duplicated.

---

## What Happens When Two Files Have the Same Name?

If two customers or teams upload a file with the same name (e.g., both upload `Monthly Report.pbix`), the MD5 hash will be **identical** for both because the hash is based only on the filename. This would cause their externalIds to collide in IDMC, and one report's objects would overwrite the other's.

### Scenario: Same Report Name Across Different Projects

This is the most common real-world collision. Different business units or projects often independently name their reports the same thing — e.g., `KPI Dashboard.pbix` exists in both the Finance project and the Operations project.

| Project | File | Hash | Result |
|---|---|---|---|
| Finance | `KPI Dashboard.pbix` | `a3f2c1d4` | `KPI_Dashboard_a3f2c1d4/...` |
| Operations | `KPI Dashboard.pbix` | `a3f2c1d4` | `KPI_Dashboard_a3f2c1d4/...` ❌ collision |

Because the hash is computed from the filename only, both produce the **same externalId prefix** and the second upload will overwrite the first in IDMC.

**What to do:** Before placing files in `input/`, rename each file to include its project name:

```
Finance_KPI Dashboard.pbix
Operations_KPI Dashboard.pbix
```

This produces distinct hashes and fully isolated externalId namespaces:

```
Finance_KPI_Dashboard_<hash1>/...
Operations_KPI_Dashboard_<hash2>/...
```

A simple convention like `<Project>_<ReportName>.pbix` across all teams prevents this entirely.

---

### How to Avoid Collisions in General

**Option 1 — Add project prefix before placing in `input/` (recommended)**

Rename files with a project or team prefix before running the script:

```
Monthly Report - TeamA.pbix
Monthly Report - TeamB.pbix
```

This produces different hashes and fully separate externalId namespaces:
```
Monthly_Report_-_TeamA_<hash1>/...
Monthly_Report_-_TeamB_<hash2>/...
```

**Option 2 — Use separate `input/` runs**

Process each project's files in a separate script run, keeping their PBIX files in separate folders, and upload the resulting zips to separate Custom Scanner resources in MCC.

**Option 3 — Enforce a naming convention across all teams**

Establish a standard so filenames are always unique organisation-wide:

```
SOCAR_Finance_Monthly Report.pbix
SOCAR_Operations_Monthly Report.pbix
```

> **Note:** The script does not detect filename collisions. It is the user's responsibility to ensure all filenames in the `input/` folder are unique across projects and teams before running.

---

## Parameters (top of script)

| Parameter | Default | Description |
|---|---|---|
| `DEBUG` | `False` | Set to `True` to keep `tmp/` and `out/` folders after the run for inspection |
| `STATS` | `True` | Set to `False` to opt out of anonymous usage statistics |
| `PBIX_OWNED_TABLE` | `Unique_Projects` | Internal Power BI table name — its `core.Resource` entry is named `PowerBI_Projects` |
| `PBIX_OWNED_RESOURCE_NAME` | `PowerBI_Projects` | Display name used in IDMC for the internal Power BI resource |

---

## Output

| File | Description |
|---|---|
| `output/powerbi.zip` | Upload this to the Custom Scanner in MCC |
| `output/powerbi/` | Same CSV files unzipped, for inspection |

### CSV files inside the zip

| File | Contents |
|---|---|
| `custom.PowerBI.v3.Report.csv` | One row per PBIX file |
| `custom.PowerBI.v3.Section.csv` | One row per report page |
| `custom.PowerBI.v3.Visual.csv` | One row per visual |
| `custom.PowerBI.v3.Field.csv` | One row per field with FieldType classification |
| `custom.PowerBI.v3.Dataset.csv` | PBIX-side dataset wrappers |
| `core.Resource.csv` | IDMC core resource entries (all `core.reference=TRUE`) |
| `core.DataSource.csv` | IDMC core data source entries |
| `core.DataSet.csv` | IDMC core dataset entries |
| `core.DataElement.csv` | IDMC core data element entries |
| `links.csv` | All lineage relationships |

---

## Known Limitations

- Only fields **visible in report visuals** are captured. Model-only tables, DAX measures not placed on any page, relationship key columns, and sort columns are not extractable from the Layout file alone.
- To capture the full data model, export as `.pbit` (Power BI Template) or use `pbi-tools extract`. See `ANALYSIS_REPORT.md` for details.
