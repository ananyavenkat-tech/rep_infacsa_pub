#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
SQL Lineage Extractor

This script extracts lineage information from SQL statements using the sqllineage package.
It reads SQL files and identifies:
  - Source tables (input data)
  - Target tables (output data)
  - Table relationships
  - Column-level lineage (when metadata available)

Author: Data Engineering Team
Date: May 14, 2026
"""

import os
import json
import argparse
from pathlib import Path
from sqllineage.runner import LineageRunner
from sqllineage.exceptions import SQLLineageException


class SQLLineageExtractor:
    """
    Extracts SQL lineage information from SQL scripts.
    
    Attributes:
        dialect (str): SQL dialect to use (ansi, sparksql, mysql, etc.)
        verbose (bool): Enable verbose logging
    """
    
    def __init__(self, dialect='ansi', verbose=False):
        """
        Initialize the SQL Lineage Extractor.
        
        Args:
            dialect (str): SQL dialect for parsing. Default: 'ansi'
            verbose (bool): Enable verbose output. Default: False
        """
        self.dialect = dialect
        self.verbose = verbose
        
    def preprocess_sql(self, sql_content):
        """
        Preprocess SQL to remove dialect-specific syntax.
        
        Removes:
            - SECURITY INVOKER (Databricks)
            - RETURNS STRUCT (Databricks UDF)
            - USING LANGUAGE (Databricks UDF)
        
        Args:
            sql_content (str): Raw SQL content
            
        Returns:
            str: Cleaned SQL content
        """
        import re
        
        # Remove SECURITY INVOKER clause (Databricks-specific)
        sql_content = re.sub(
            r'\bSECURITY\s+INVOKER\s+',
            '',
            sql_content,
            flags=re.IGNORECASE
        )
        
        # Remove RETURNS clause with STRUCT (Databricks UDF syntax)
        sql_content = re.sub(
            r'\bRETURNS\s+STRUCT\s*\([^)]*\)\s*',
            '',
            sql_content,
            flags=re.IGNORECASE
        )
        
        # Remove USING LANGUAGE clause (Databricks UDF syntax)
        sql_content = re.sub(
            r'\bUSING\s+LANGUAGE\s+\w+\s*',
            '',
            sql_content,
            flags=re.IGNORECASE
        )
        
        if self.verbose:
            print("[DEBUG] SQL preprocessing completed: removed dialect-specific syntax")
        
        return sql_content
    
    def extract_lineage(self, sql_statement):
        """
        Extract lineage from a single SQL statement.
        
        Args:
            sql_statement (str): SQL statement to parse
            
        Returns:
            dict: Lineage information with keys:
                - source_tables: List of source table names
                - target_tables: List of target table names
                - raw_source_tables: Full source table references
                - raw_target_tables: Full target table references
                - success: Boolean indicating successful parsing
                - error: Error message if parsing failed
        """
        try:
            # Preprocess SQL
            cleaned_sql = self.preprocess_sql(sql_statement)
            
            # Parse with sqllineage
            runner = LineageRunner(
                cleaned_sql,
                dialect=self.dialect
            )
            
            # Extract lineage
            source_tables = list(runner.source_tables)
            target_tables = list(runner.target_tables)
            
            result = {
                'success': True,
                'source_tables': [str(t) for t in source_tables],
                'target_tables': [str(t) for t in target_tables],
                'raw_source_tables': source_tables,
                'raw_target_tables': target_tables,
                'error': None
            }
            
            if self.verbose:
                print(f"[DEBUG] Source tables: {result['source_tables']}")
                print(f"[DEBUG] Target tables: {result['target_tables']}")
            
            return result
            
        except SQLLineageException as e:
            error_msg = f"SQLLineage parsing failed: {str(e)}"
            if self.verbose:
                print(f"[WARNING] {error_msg}")
            return {
                'success': False,
                'source_tables': [],
                'target_tables': [],
                'raw_source_tables': [],
                'raw_target_tables': [],
                'error': error_msg
            }
        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            if self.verbose:
                print(f"[ERROR] {error_msg}")
            return {
                'success': False,
                'source_tables': [],
                'target_tables': [],
                'raw_source_tables': [],
                'raw_target_tables': [],
                'error': error_msg
            }
    
    def extract_from_sql_file(self, file_path):
        """
        Extract lineage from all statements in a SQL file.
        
        Args:
            file_path (str): Path to SQL file
            
        Returns:
            dict: File analysis results with:
                - file_path: Input file path
                - total_statements: Total SQL statements found
                - parsed_count: Successfully parsed statements
                - failed_count: Failed statements
                - statements: List of lineage results per statement
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"SQL file not found: {file_path}")
        
        if self.verbose:
            print(f"\n[INFO] Processing file: {file_path}")
        
        try:
            # Read file
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract SQL statements (split by semicolon)
            statements = [
                stmt.strip()
                for stmt in content.split(';')
                if stmt.strip()
            ]
            
            if self.verbose:
                print(f"[INFO] Found {len(statements)} SQL statement(s)")
            
            # Parse each statement
            results = []
            parsed_count = 0
            failed_count = 0
            
            for idx, statement in enumerate(statements, 1):
                if self.verbose:
                    print(f"\n[DEBUG] Processing statement #{idx}")
                
                lineage = self.extract_lineage(statement)
                lineage['statement_number'] = idx
                lineage['statement_text'] = statement[:100] + '...' if len(statement) > 100 else statement
                
                results.append(lineage)
                
                if lineage['success']:
                    parsed_count += 1
                else:
                    failed_count += 1
            
            return {
                'file_path': file_path,
                'total_statements': len(statements),
                'parsed_count': parsed_count,
                'failed_count': failed_count,
                'statements': results
            }
            
        except Exception as e:
            print(f"[ERROR] Failed to process file {file_path}: {str(e)}")
            return {
                'file_path': file_path,
                'total_statements': 0,
                'parsed_count': 0,
                'failed_count': 1,
                'statements': [],
                'error': str(e)
            }
    
    def extract_from_folder(self, folder_path):
        """
        Extract lineage from all SQL files in a folder.
        
        Args:
            folder_path (str): Path to folder containing SQL files
            
        Returns:
            dict: Summary of all files processed
        """
        if not os.path.isdir(folder_path):
            raise NotADirectoryError(f"Folder not found: {folder_path}")
        
        if self.verbose:
            print(f"[INFO] Scanning folder: {folder_path}")
        
        # Find all SQL files
        sql_files = list(Path(folder_path).glob('*.sql'))
        
        if not sql_files:
            print(f"[WARNING] No SQL files found in {folder_path}")
            return {
                'folder_path': folder_path,
                'files_found': 0,
                'total_statements': 0,
                'total_parsed': 0,
                'total_failed': 0,
                'files': []
            }
        
        if self.verbose:
            print(f"[INFO] Found {len(sql_files)} SQL file(s)")
        
        # Process each file
        all_results = []
        total_statements = 0
        total_parsed = 0
        total_failed = 0
        
        for sql_file in sorted(sql_files):
            file_result = self.extract_from_sql_file(str(sql_file))
            all_results.append(file_result)
            
            total_statements += file_result['total_statements']
            total_parsed += file_result['parsed_count']
            total_failed += file_result['failed_count']
        
        return {
            'folder_path': folder_path,
            'files_found': len(sql_files),
            'total_statements': total_statements,
            'total_parsed': total_parsed,
            'total_failed': total_failed,
            'files': all_results
        }
    
    def print_lineage_report(self, lineage_result):
        """
        Print a formatted lineage report.
        
        Args:
            lineage_result (dict): Lineage extraction result
        """
        print("\n" + "="*80)
        print("SQL LINEAGE EXTRACTION REPORT")
        print("="*80)
        
        if 'file_path' in lineage_result and 'statements' in lineage_result:
            # Single file report
            self._print_file_report(lineage_result)
        elif 'folder_path' in lineage_result:
            # Folder report
            self._print_folder_report(lineage_result)
    
    def _print_file_report(self, file_result):
        """Print report for a single file."""
        print(f"\nFile: {file_result['file_path']}")
        print(f"Total Statements: {file_result['total_statements']}")
        print(f"Parsed: {file_result['parsed_count']}")
        print(f"Failed: {file_result['failed_count']}")
        
        print("\n" + "-"*80)
        
        for stmt in file_result['statements']:
            print(f"\nStatement #{stmt['statement_number']}: {stmt['statement_text']}")
            
            if stmt['success']:
                print(f"  ✓ Status: SUCCESS")
                if stmt['source_tables']:
                    print(f"  Source Tables: {', '.join(stmt['source_tables'])}")
                if stmt['target_tables']:
                    print(f"  Target Tables: {', '.join(stmt['target_tables'])}")
            else:
                print(f"  ✗ Status: FAILED")
                print(f"  Error: {stmt['error']}")
    
    def _print_folder_report(self, folder_result):
        """Print report for a folder."""
        print(f"\nFolder: {folder_result['folder_path']}")
        print(f"Files Found: {folder_result['files_found']}")
        print(f"Total Statements: {folder_result['total_statements']}")
        print(f"Total Parsed: {folder_result['total_parsed']}")
        print(f"Total Failed: {folder_result['total_failed']}")
        
        print("\n" + "-"*80)
        
        for file_result in folder_result['files']:
            print(f"\nFile: {os.path.basename(file_result['file_path'])}")
            print(f"  Statements: {file_result['total_statements']} | Parsed: {file_result['parsed_count']} | Failed: {file_result['failed_count']}")
            
            for stmt in file_result['statements']:
                if stmt['success'] and (stmt['source_tables'] or stmt['target_tables']):
                    print(f"  Statement #{stmt['statement_number']}:")
                    if stmt['source_tables']:
                        print(f"    → From: {', '.join(stmt['source_tables'])}")
                    if stmt['target_tables']:
                        print(f"    → To: {', '.join(stmt['target_tables'])}")
    
    def export_to_json(self, lineage_result, output_file):
        """
        Export lineage results to JSON file.
        
        Args:
            lineage_result (dict): Lineage extraction result
            output_file (str): Path to output JSON file
        """
        # Convert non-serializable objects to strings
        def serialize_result(result):
            if isinstance(result, dict):
                return {
                    k: serialize_result(v)
                    for k, v in result.items()
                }
            elif isinstance(result, list):
                return [serialize_result(item) for item in result]
            else:
                return str(result)
        
        serialized = serialize_result(lineage_result)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(serialized, f, indent=2)
        
        print(f"\n[INFO] Results exported to: {output_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Extract SQL lineage information from SQL files/folders'
    )
    
    # Input options
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '-f', '--file',
        type=str,
        help='Path to single SQL file'
    )
    group.add_argument(
        '-d', '--folder',
        type=str,
        help='Path to folder containing SQL files'
    )
    
    # SQL options
    parser.add_argument(
        '--dialect',
        type=str,
        default='ansi',
        help='SQL dialect (default: ansi). Examples: sparksql, mysql, postgresql, etc.'
    )
    
    # Output options
    parser.add_argument(
        '-o', '--output',
        type=str,
        help='Export results to JSON file'
    )
    
    # Logging
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Create extractor
    extractor = SQLLineageExtractor(
        dialect=args.dialect,
        verbose=args.verbose
    )
    
    try:
        # Process input
        if args.file:
            if args.verbose:
                print(f"[INFO] Processing SQL file: {args.file}")
            result = extractor.extract_from_sql_file(args.file)
        else:
            if args.verbose:
                print(f"[INFO] Processing folder: {args.folder}")
            result = extractor.extract_from_folder(args.folder)
        
        # Print report
        extractor.print_lineage_report(result)
        
        # Export if requested
        if args.output:
            extractor.export_to_json(result, args.output)
        
        print("\n" + "="*80)
        print("EXTRACTION COMPLETE")
        print("="*80)
        
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        exit(1)


if __name__ == '__main__':
    main()
