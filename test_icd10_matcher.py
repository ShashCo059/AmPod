#!/usr/bin/env python
"""
Test script for ICD-10 code matcher
Run this to verify the code_matcher module works correctly
"""

import sys
import os

# Add the hcc_engine to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hcc_engine.code_matcher import get_medical_codes

# Test data
test_insights = [
    "Hypertension",
    "Knee Injury",
    "Diabetes Mellitus",
    "Chest Pain",
    "Asthma"
]

print("=" * 80)
print("ICD-10 CODE MATCHER TEST")
print("=" * 80)
print(f"\nTesting with {len(test_insights)} conditions:")
for i, insight in enumerate(test_insights, 1):
    print(f"  {i}. {insight}")

print("\n" + "-" * 80)
print("Matching conditions to ICD-10 database...")
print("-" * 80 + "\n")

# Test the function
matched_codes = get_medical_codes(test_insights, threshold=0.4)

if matched_codes:
    print(f"✓ Found {len(matched_codes)} matches:\n")
    
    # Print formatted results
    for i, result in enumerate(matched_codes, 1):
        print(f"{i}. Condition: {result['Extracted Condition']}")
        print(f"   Matched: {result['Matched Disease/Injury']}")
        print(f"   ICD-10 Code: {result['ICD-10 Code']}")
        print(f"   Category: {result['Category']}")
        print(f"   Confidence: {result['Confidence']:.1%}")
        print()
else:
    print("✗ No matches found. Please ensure:")
    print("  1. icd10.csv exists in d:\\Agent-pod\\")
    print("  2. CSV has 'Description' and 'Code' columns")
    print("  3. CSV contains medical condition data")

print("=" * 80)
