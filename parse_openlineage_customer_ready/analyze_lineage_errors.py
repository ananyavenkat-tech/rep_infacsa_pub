#!/usr/bin/env python3
"""
Analyzes OpenLineage NDJSON input files to identify which ones cannot generate
directional lineage or dataset dataflow, and documents the reasons.

Outputs: Error_file.csv with detailed information about problematic events
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List, Tuple
import csv
from datetime import datetime


class LineageAnalyzer:
    """Analyzes NDJSON files to identify lineage generation failures."""
    
    def __init__(self, input_path: str, output_path: str):
        self.input_path = input_path
        self.output_path = output_path
        self.errors = []
        self.file_summary = {}
        
    def run(self):
        """Execute the analysis."""
        input_files = self._discover_input_files(self.input_path)
        
        for input_file in input_files:
            print(f"Analyzing: {input_file}")
            self._analyze_file(input_file)
        
        # Write error report
        self._write_error_report()
        print(f"\nError report written to: Error_file.csv")
        
    def _discover_input_files(self, in_path: str) -> List[str]:
        """Return JSONL/NDJSON input files from a file path or directory."""
        path = Path(in_path)
        if path.is_file():
            return [str(path)]
        if not path.is_dir():
            raise FileNotFoundError(f"Input path does not exist: {in_path}")
        
        supported_extensions = {".jsonl", ".ndjson"}
        files = [
            str(p)
            for p in sorted(path.iterdir())
            if p.is_file() and p.suffix.lower() in supported_extensions
        ]
        if not files:
            raise FileNotFoundError(f"No .jsonl or .ndjson files found in: {in_path}")
        return files
    
    def _analyze_file(self, input_file: str):
        """Analyze a single NDJSON file."""
        filename = os.path.basename(input_file)
        valid_count = 0
        invalid_json_count = 0
        no_inputs_count = 0
        no_outputs_count = 0
        no_job_count = 0
        no_schema_count = 0
        no_column_lineage_count = 0
        lineage_ready_count = 0
        
        problematic_events = []
        
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        event = json.loads(line)
                        valid_count += 1
                        
                        # Analyze this event
                        error_reasons = self._check_event(event)
                        
                        if error_reasons:
                            for reason in error_reasons:
                                problematic_events.append({
                                    "filename": filename,
                                    "line_number": line_number,
                                    "run_id": event.get("run", {}).get("runId", "N/A"),
                                    "job_name": event.get("job", {}).get("name", "N/A"),
                                    "job_namespace": event.get("job", {}).get("namespace", "N/A"),
                                    "event_type": event.get("eventType", "N/A"),
                                    "error_type": reason,
                                    "inputs_count": len(event.get("inputs", [])),
                                    "outputs_count": len(event.get("outputs", [])),
                                    "has_job": "job" in event,
                                    "has_schema": self._has_schema(event),
                                    "has_column_lineage": self._has_column_lineage(event),
                                })
                            
                            if "No inputs" in error_reasons:
                                no_inputs_count += 1
                            if "No outputs" in error_reasons:
                                no_outputs_count += 1
                            if "No job information" in error_reasons:
                                no_job_count += 1
                            if "No schema facet" in error_reasons:
                                no_schema_count += 1
                            if "No column lineage facet" in error_reasons:
                                no_column_lineage_count += 1
                        else:
                            lineage_ready_count += 1
                            
                    except json.JSONDecodeError as e:
                        invalid_json_count += 1
                        problematic_events.append({
                            "filename": filename,
                            "line_number": line_number,
                            "run_id": "N/A",
                            "job_name": "N/A",
                            "job_namespace": "N/A",
                            "event_type": "N/A",
                            "error_type": f"Invalid JSON: {str(e)[:100]}",
                            "inputs_count": 0,
                            "outputs_count": 0,
                            "has_job": False,
                            "has_schema": False,
                            "has_column_lineage": False,
                        })
        
        except Exception as e:
            print(f"ERROR reading file {filename}: {e}")
            return
        
        # Store file summary
        self.file_summary[filename] = {
            "total_lines": valid_count + invalid_json_count,
            "valid_events": valid_count,
            "invalid_json": invalid_json_count,
            "no_inputs": no_inputs_count,
            "no_outputs": no_outputs_count,
            "no_job": no_job_count,
            "no_schema": no_schema_count,
            "no_column_lineage": no_column_lineage_count,
            "lineage_ready": lineage_ready_count,
        }
        
        self.errors.extend(problematic_events)
    
    def _check_event(self, event: Dict[str, Any]) -> List[str]:
        """Check if event can generate lineage. Return list of error reasons if not."""
        reasons = []
        
        # Check for inputs
        inputs = event.get("inputs")
        if not inputs or len(inputs) == 0:
            reasons.append("No inputs - Cannot generate DataSetDataFlow")
        
        # Check for outputs
        outputs = event.get("outputs")
        if not outputs or len(outputs) == 0:
            reasons.append("No outputs - Cannot generate DataSetDataFlow")
        
        # Check for job
        job = event.get("job")
        if not job or not job.get("name"):
            reasons.append("No job information - Cannot generate Task")
        
        # Check for schema facet (required for column-level lineage)
        if not self._has_schema(event):
            reasons.append("No schema facet - Cannot generate column-level DataElement")
        
        # Check for column lineage facet (required for directional lineage)
        if not self._has_column_lineage(event):
            reasons.append("No column lineage facet - Cannot generate DirectionalDataFlow")
        
        return reasons
    
    def _has_schema(self, event: Dict[str, Any]) -> bool:
        """Check if event has schema information."""
        for dataset_list in [event.get("inputs", []), event.get("outputs", [])]:
            for dataset in dataset_list:
                facets = dataset.get("facets", {})
                schema = facets.get("schema", {})
                fields = schema.get("fields", [])
                if fields and len(fields) > 0:
                    return True
        return False
    
    def _has_column_lineage(self, event: Dict[str, Any]) -> bool:
        """Check if event has column lineage facet."""
        job = event.get("job", {})
        facets = job.get("facets", {})
        column_lineage = facets.get("columnLineage")
        return column_lineage is not None and len(column_lineage.get("fields", [])) > 0
    
    def _write_error_report(self):
        """Write comprehensive error report to CSV."""
        report_path = os.path.join(self.output_path, "Error_file.csv")
        
        # Write main error details
        if self.errors:
            fieldnames = [
                "filename",
                "line_number",
                "run_id",
                "job_name",
                "job_namespace",
                "event_type",
                "error_type",
                "inputs_count",
                "outputs_count",
                "has_job",
                "has_schema",
                "has_column_lineage",
                "reason_description"
            ]
            
            with open(report_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for error in self.errors:
                    row = error.copy()
                    # Add reason description
                    reason_map = {
                        "No inputs - Cannot generate DataSetDataFlow": "Dataset input is missing. Lineage flow cannot be established.",
                        "No outputs - Cannot generate DataSetDataFlow": "Dataset output is missing. Lineage flow cannot be established.",
                        "No job information - Cannot generate Task": "Job name/namespace missing. Task metadata cannot be created.",
                        "No schema facet - Cannot generate column-level DataElement": "Schema information missing. Cannot map columns (data elements) in the lineage.",
                        "No column lineage facet - Cannot generate DirectionalDataFlow": "Column-level lineage not available. Cannot create directional flow between columns.",
                    }
                    row["reason_description"] = reason_map.get(row["error_type"], row["error_type"])
                    writer.writerow(row)
        
        # Write summary report
        summary_path = os.path.join(self.output_path, "Lineage_Analysis_Summary.csv")
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "filename",
                "total_events",
                "valid_events",
                "invalid_json",
                "events_with_no_inputs",
                "events_with_no_outputs",
                "events_with_no_job",
                "events_with_no_schema",
                "events_with_no_column_lineage",
                "lineage_ready_events",
                "can_generate_directional_lineage_percent",
                "can_generate_dataset_dataflow_percent"
            ])
            writer.writeheader()
            
            for filename, summary in self.file_summary.items():
                total = summary["valid_events"]
                lineage_ready = summary["lineage_ready"]
                directional_ready = total - summary["no_column_lineage"] if total > 0 else 0
                dataflow_ready = total - (summary["no_inputs"] + summary["no_outputs"]) if total > 0 else 0
                
                writer.writerow({
                    "filename": filename,
                    "total_events": total,
                    "valid_events": summary["valid_events"],
                    "invalid_json": summary["invalid_json"],
                    "events_with_no_inputs": summary["no_inputs"],
                    "events_with_no_outputs": summary["no_outputs"],
                    "events_with_no_job": summary["no_job"],
                    "events_with_no_schema": summary["no_schema"],
                    "events_with_no_column_lineage": summary["no_column_lineage"],
                    "lineage_ready_events": lineage_ready,
                    "can_generate_directional_lineage_percent": f"{(directional_ready / total * 100):.1f}%" if total > 0 else "N/A",
                    "can_generate_dataset_dataflow_percent": f"{(dataflow_ready / total * 100):.1f}%" if total > 0 else "N/A",
                })
        
        # Write key findings
        findings_path = os.path.join(self.output_path, "Lineage_Error_Analysis.txt")
        with open(findings_path, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write("OPENLINEAGE ERROR ANALYSIS REPORT\n")
            f.write("=" * 80 + "\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("SUMMARY BY FILE\n")
            f.write("-" * 80 + "\n")
            for filename, summary in self.file_summary.items():
                f.write(f"\nFile: {filename}\n")
                f.write(f"  Total valid events: {summary['valid_events']}\n")
                f.write(f"  Invalid JSON lines: {summary['invalid_json']}\n")
                f.write(f"  Events with NO INPUTS: {summary['no_inputs']}\n")
                f.write(f"  Events with NO OUTPUTS: {summary['no_outputs']}\n")
                f.write(f"  Events with NO JOB INFO: {summary['no_job']}\n")
                f.write(f"  Events with NO SCHEMA: {summary['no_schema']}\n")
                f.write(f"  Events with NO COLUMN LINEAGE: {summary['no_column_lineage']}\n")
                f.write(f"  Events ready for lineage: {summary['lineage_ready']}\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write("REASONS WHY FILES CANNOT GENERATE LINEAGE\n")
            f.write("=" * 80 + "\n\n")
            
            f.write("1. NO INPUTS\n")
            f.write("   Why it prevents lineage:\n")
            f.write("   - DataSetDataFlow requires source datasets (inputs)\n")
            f.write("   - Without inputs, the lineage origin cannot be established\n")
            f.write("   - No parent datasets can be linked to output datasets\n\n")
            
            f.write("2. NO OUTPUTS\n")
            f.write("   Why it prevents lineage:\n")
            f.write("   - DataSetDataFlow requires target datasets (outputs)\n")
            f.write("   - Without outputs, the lineage destination cannot be established\n")
            f.write("   - Cannot complete the flow from inputs to results\n\n")
            
            f.write("3. NO JOB INFORMATION\n")
            f.write("   Why it prevents lineage:\n")
            f.write("   - Tasks (jobs) are the intermediate nodes connecting datasets\n")
            f.write("   - Without job name/namespace, cannot create Task metadata\n")
            f.write("   - Links between datasets and processing steps cannot be formed\n\n")
            
            f.write("4. NO SCHEMA FACET\n")
            f.write("   Why it prevents column-level lineage:\n")
            f.write("   - DataElement (columns) require schema information\n")
            f.write("   - Without schema, cannot identify which columns exist in each dataset\n")
            f.write("   - Column-level lineage and transformations cannot be mapped\n\n")
            
            f.write("5. NO COLUMN LINEAGE FACET\n")
            f.write("   Why it prevents directional lineage:\n")
            f.write("   - DirectionalDataFlow requires columnLineage facet\n")
            f.write("   - Without columnLineage, cannot trace how input columns map to output columns\n")
            f.write("   - Field-level transformations cannot be visualized\n")
            f.write("   - Only table-level (dataset) lineage can be generated, not column-level\n\n")
            
            f.write("6. INVALID JSON\n")
            f.write("   Why it prevents lineage:\n")
            f.write("   - Malformed JSON cannot be parsed\n")
            f.write("   - Event structure is invalid or corrupted\n")
            f.write("   - Line is skipped entirely from processing\n\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write("LINEAGE TYPES AND REQUIREMENTS\n")
            f.write("=" * 80 + "\n\n")
            
            f.write("TABLE-LEVEL LINEAGE (DataSetDataFlow)\n")
            f.write("  Minimum requirements:\n")
            f.write("    ✓ At least one input dataset\n")
            f.write("    ✓ At least one output dataset\n")
            f.write("    ✓ Job/task information (job.name)\n")
            f.write("  Generates: core.DataSetDataFlow links\n\n")
            
            f.write("COLUMN-LEVEL LINEAGE (DirectionalDataFlow)\n")
            f.write("  Minimum requirements:\n")
            f.write("    ✓ All of the above (for table-level)\n")
            f.write("    ✓ Schema facet with field definitions (inputs and outputs)\n")
            f.write("    ✓ columnLineage facet in job facets\n")
            f.write("  Generates: DirectionalDataFlow links (input DataElement → Calculation → output DataElement)\n\n")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Analyze OpenLineage NDJSON files to identify lineage generation issues"
    )
    parser.add_argument(
        "--in", "-i",
        dest="input",
        required=True,
        help="Input directory or file path with NDJSON files"
    )
    parser.add_argument(
        "--out", "-o",
        dest="output",
        default="./output",
        help="Output directory for error report (default: ./output)"
    )
    
    args = parser.parse_args()
    
    # Ensure output directory exists
    os.makedirs(args.output, exist_ok=True)
    
    analyzer = LineageAnalyzer(args.input, args.output)
    analyzer.run()


if __name__ == "__main__":
    main()
