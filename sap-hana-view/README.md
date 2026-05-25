# SAP HANA Calculation View to CDGC CSV Converter

This tool parses SAP HANA Calculation View XML files (`.hdbcalculationview`) and dynamically generates a ZIP file containing metadata CSV files that match the Informatica CDGC custom scanner import format. It dynamically reads a provided CDGC model JSON file to extract the package name, classes, and associations.

## Folder Structure
- `hana_calcview_to_cdgc_csv_v3.py` : The main execution script.
- `SAPHANACalcScript_v3.json` : The CDGC Model file used to dynamically extract metadata definitions.
- `input/` : Directory to place your SAP HANA calculation view source files (`*.hdbcalculationview`).
- `output/` : Directory where the resulting `hana_cdgc_import_v3.zip` will be generated.
- `requirements.txt` : Dependency file. Note: This script only uses built-in standard Python libraries, so no external installations are required!
- `venv/` : A Python virtual environment isolated for this project.

## How to Setup
You can optionally use the provided virtual environment:
1. Open Windows PowerShell in this directory.
2. Activate the virtual environment:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```
*(If you encounter a PowerShell execution policy error, run `Set-ExecutionPolicy Unrestricted -Scope Process` first).*

## How to Run
1. Place all your `.hdbcalculationview` XML files into the `input` directory.
2. Ensure that your CDGC JSON model file (`SAPHANACalcScript_v3.json`) is in the same directory as the script.
3. Run the script using Python. You can do this either by activating your virtual environment first:
   ```powershell
   python hana_calcview_to_cdgc_csv_v3.py
   ```
   Or by directly pointing to the virtual environment's Python executable:
   ```powershell
   .\venv\Scripts\python.exe hana_calcview_to_cdgc_csv_v3.py
   ```
*(By default, without passing arguments, the script will automatically pick up files from the `input` directory and use the `SAPHANACalcScript_v3.json` file in the root folder).*

### Custom Paths
If you want to run the script using different source or model paths, pass them as arguments:
```powershell
python hana_calcview_to_cdgc_csv_v3.py "C:\Path\To\Input\Directory" "C:\Path\To\Model.json"
```

## Output
The script will print summary logs to your console and output the metadata payload inside the `output` folder:
`output/hana_cdgc_import_v3.zip`
