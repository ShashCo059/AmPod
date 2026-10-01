from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEMO_DATA_FILE = WORKSPACE_ROOT / "Epic_Inspired_USA_50_Patient_Demo.json"


def load_ehr_data(path: Path | None = None) -> dict[str, Any]:
    source = path or DEMO_DATA_FILE
    with source.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("sheets"), dict):
        raise ValueError("The EHR demo JSON must contain a sheets object.")
    return payload


def save_ehr_data(payload: dict[str, Any], path: Path | None = None) -> None:
    destination = path or DEMO_DATA_FILE
    temporary_path = destination.with_name(f"{destination.name}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def list_patients(path: Path | None = None) -> list[dict[str, Any]]:
    payload = load_ehr_data(path)
    return payload["sheets"].get("Patient_Master", {}).get("records", [])


def get_patient_chart(patient_id: str, path: Path | None = None) -> dict[str, Any]:
    payload = load_ehr_data(path)
    patient = next(
        (
            record
            for record in payload["sheets"].get("Patient_Master", {}).get("records", [])
            if record.get("Patient_ID") == patient_id
        ),
        None,
    )
    if patient is None:
        raise KeyError(patient_id)

    records: dict[str, list[dict[str, Any]]] = {}
    headers: dict[str, list[str]] = {}
    for name, sheet in payload["sheets"].items():
        if sheet.get("structure") != "records":
            continue
        patient_records = [
            record
            for record in sheet.get("records", [])
            if record.get("Patient_ID") == patient_id
        ]
        records[name] = patient_records
        headers[name] = sheet.get("headers", [])
    return {"patient": patient, "records": records, "headers": headers}


def create_patient(values: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    payload = load_ehr_data(path)
    sheet = payload["sheets"].get("Patient_Master")
    if not isinstance(sheet, dict) or not sheet.get("headers"):
        raise ValueError("Patient_Master is missing its column definitions.")

    first_name = str(values.get("First_Name", "")).strip()
    last_name = str(values.get("Last_Name", "")).strip()
    if not first_name or not last_name:
        raise ValueError("First name and last name are required.")
    dob = str(values.get("DOB", "")).strip()
    try:
        birth_date = date.fromisoformat(dob[:10])
    except ValueError as error:
        raise ValueError("DOB must be an ISO date such as 1980-04-23.") from error

    existing = sheet.get("records", [])
    next_id = _next_numeric_suffix((str(row.get("Patient_ID", "")) for row in existing), 100001)
    next_mrn = _next_numeric_suffix((str(row.get("MRN", "")) for row in existing), 500001)
    patient = {header: "" for header in sheet["headers"]}
    patient.update({key: value for key, value in values.items() if key in patient})
    patient.update(
        {
            "Patient_ID": f"P{next_id}",
            "MRN": f"MRN{next_mrn}",
            "First_Name": first_name,
            "Last_Name": last_name,
            "Legal_Name": f"{first_name} {last_name}",
            "DOB": birth_date.isoformat(),
            "Age": _age_on(birth_date, date.today()),
            "Record_Status": "Active",
        }
    )
    sheet.setdefault("records", []).append(patient)
    save_ehr_data(payload, path)
    return patient


def update_patient(patient_id: str, values: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    payload = load_ehr_data(path)
    sheet = payload["sheets"].get("Patient_Master", {})
    headers = set(sheet.get("headers", []))
    patient = next(
        (row for row in sheet.get("records", []) if row.get("Patient_ID") == patient_id),
        None,
    )
    if patient is None:
        raise KeyError(patient_id)
    patient.update({key: value for key, value in values.items() if key in headers and key != "Patient_ID"})
    patient["Patient_ID"] = patient_id
    if "First_Name" in values or "Last_Name" in values:
        patient["Legal_Name"] = " ".join(
            part for part in (str(patient.get("First_Name", "")).strip(), str(patient.get("Last_Name", "")).strip())
            if part
        )
    if "DOB" in values:
        try:
            birth_date = date.fromisoformat(str(patient.get("DOB", ""))[:10])
        except ValueError as error:
            raise ValueError("DOB must be an ISO date such as 1980-04-23.") from error
        patient["Age"] = _age_on(birth_date, date.today())
    save_ehr_data(payload, path)
    return patient


def replace_patient_records(
    patient_id: str,
    sheet_name: str,
    records: list[dict[str, Any]],
    path: Path | None = None,
) -> list[dict[str, Any]]:
    payload = load_ehr_data(path)
    _require_patient(payload, patient_id)
    sheet = payload["sheets"].get(sheet_name)
    if not isinstance(sheet, dict) or sheet.get("structure") != "records":
        raise ValueError(f"Unknown editable patient record sheet: {sheet_name}")
    if sheet_name == "Patient_Master":
        raise ValueError("Use the patient update endpoint to edit demographics.")

    headers = set(sheet.get("headers", []))
    normalized_records = []
    for record in records:
        normalized = {key: value for key, value in record.items() if key in headers}
        if "Patient_ID" in headers:
            normalized["Patient_ID"] = patient_id
        normalized_records.append(normalized)

    updated_records = []
    inserted = False
    for record in sheet.get("records", []):
        if record.get("Patient_ID") == patient_id:
            if not inserted:
                updated_records.extend(normalized_records)
                inserted = True
            continue
        updated_records.append(record)
    if not inserted:
        updated_records.extend(normalized_records)
    sheet["records"] = updated_records
    save_ehr_data(payload, path)
    return normalized_records


def create_patient_record(
    patient_id: str,
    sheet_name: str,
    values: dict[str, Any],
    path: Path | None = None,
) -> dict[str, Any]:
    payload = load_ehr_data(path)
    _require_patient(payload, patient_id)
    sheet = payload["sheets"].get(sheet_name)
    if not isinstance(sheet, dict) or sheet.get("structure") != "records" or sheet_name == "Patient_Master":
        raise ValueError(f"Unknown editable patient record sheet: {sheet_name}")

    headers = sheet.get("headers", [])
    record = {header: "" for header in headers}
    record.update({key: value for key, value in values.items() if key in headers and key != "Patient_ID"})
    record["Patient_ID"] = patient_id

    id_fields = {
        "Encounters": ("Encounter_ID", "ENC"),
        "Problems": ("Problem_ID", "PRB"),
        "Allergies": ("Allergy_ID", "ALG"),
        "Medications": ("Medication_ID", "MED"),
        "Vitals": ("Vital_ID", "VIT"),
        "Labs": ("Lab_ID", "LAB"),
        "Orders_Procedures": ("Order_ID", "ORD"),
        "Immunizations": ("Immunization_ID", "IMM"),
        "Appointments": ("Appointment_ID", "APT"),
        "Clinical_Notes": ("Note_ID", "NOTE"),
        "Insurance": ("Coverage_ID", "COV"),
        "Care_Team": ("Care_Team_ID", "CARE"),
    }
    id_field, prefix = id_fields.get(sheet_name, ("", ""))
    if id_field:
        existing_ids = (str(row.get(id_field, "")) for row in sheet.get("records", []))
        record[id_field] = f"{prefix}{_next_numeric_suffix(existing_ids, 1):05d}"
    sheet.setdefault("records", []).append(record)
    save_ehr_data(payload, path)
    return record


def delete_patient(patient_id: str, path: Path | None = None) -> bool:
    payload = load_ehr_data(path)
    patient_sheet = payload["sheets"].get("Patient_Master", {})
    patients = patient_sheet.get("records", [])
    if not any(row.get("Patient_ID") == patient_id for row in patients):
        raise KeyError(patient_id)

    patient_sheet["records"] = [row for row in patients if row.get("Patient_ID") != patient_id]
    for sheet in payload["sheets"].values():
        if sheet.get("structure") == "records":
            sheet["records"] = [
                row for row in sheet.get("records", [])
                if row.get("Patient_ID") != patient_id
            ]
    save_ehr_data(payload, path)
    return True


def _require_patient(payload: dict[str, Any], patient_id: str) -> dict[str, Any]:
    patient = next(
        (
            row
            for row in payload["sheets"].get("Patient_Master", {}).get("records", [])
            if row.get("Patient_ID") == patient_id
        ),
        None,
    )
    if patient is None:
        raise KeyError(patient_id)
    return patient


def _next_numeric_suffix(values, fallback: int) -> int:
    suffixes = []
    for value in values:
        match = re.search(r"(\d+)$", value)
        if match:
            suffixes.append(int(match.group(1)))
    return max(suffixes, default=fallback - 1) + 1


def _age_on(birth_date: date, as_of: date) -> int:
    return as_of.year - birth_date.year - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
