#!/usr/bin/env python
"""
Convert ICD-10 Excel file to CSV
Usage: python convert_icd10_to_csv.py <path_to_excel_file>
"""

import sys
import pandas as pd
import os

def convert_excel_to_csv(excel_file_path, output_csv_path=None):
    """
    Convert ICD-10 Excel file to CSV format.
    
    Args:
        excel_file_path: Path to the Excel file (XLSX or XLS)
        output_csv_path: Output CSV path (defaults to icd10.csv in same directory as Excel)
    """
    
    if not os.path.exists(excel_file_path):
        print(f"❌ Error: File not found: {excel_file_path}")
        return False
    
    try:
        print(f"📂 Reading Excel file: {excel_file_path}")
        
        # Try to read the Excel file
        # First, try to detect the correct sheet name
        xls = pd.ExcelFile(excel_file_path)
        print(f"   Available sheets: {xls.sheet_names}")
        
        # Read the first sheet (or adjust if you know the specific sheet name)
        sheet_name = xls.sheet_names[0]
        print(f"   Reading sheet: {sheet_name}")
        
        df = pd.read_excel(excel_file_path, sheet_name=sheet_name)
        
        print(f"   ✓ Loaded {len(df)} rows, {len(df.columns)} columns")
        print(f"   Columns: {df.columns.tolist()}")
        
        # Determine output path
        if output_csv_path is None:
            excel_dir = os.path.dirname(excel_file_path)
            output_csv_path = os.path.join(excel_dir, "icd10.csv")
        
        # Save to CSV
        df.to_csv(output_csv_path, index=False, encoding='utf-8')
        print(f"\n✓ Successfully converted!")
        print(f"   CSV saved to: {output_csv_path}")
        print(f"   Total records: {len(df)}")
        
        # Print first few rows
        print(f"\n📋 Preview (first 3 rows):")
        print(df.head(3).to_string())
        
        return True
        
    except Exception as e:
        print(f"❌ Error converting file: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convert_icd10_to_csv.py <path_to_excel_file>")
        print("\nExample:")
        print("  python convert_icd10_to_csv.py ICD10CM_2022_Codes.xlsx")
        print("  python convert_icd10_to_csv.py d:\\Downloads\\ICD10CM_2022_Codes.xlsx")
        sys.exit(1)
    
    excel_path = sys.argv[1]
    
    # Optional: specify output path as second argument
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    success = convert_excel_to_csv(excel_path, output_path)
    sys.exit(0 if success else 1)
