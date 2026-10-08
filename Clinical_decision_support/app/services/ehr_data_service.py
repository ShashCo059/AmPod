from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEMO_DATA_FILE = WORKSPACE_ROOT / "Epic_Inspired_USA_50_Patient_Demo_25_Athena_25_Care_Gap.json"
PAYER_PLANS = {
    "Athena Health Insurance": ("ATH", "Athena PPO"),
}
AUDIT_LOG_HEADERS = [
    "Audit_ID",
    "Patient_ID",
    "Timestamp",
    "Actor",
    "Action",
    "Record_Type",
    "Before",
    "After",
]


def load_ehr_data(path: Path | None = None) -> dict[str, Any]:
    source = path or DEMO_DATA_FILE
    with source.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or not isinstance(payload.get("sheets"), dict):
        raise ValueError("The EHR demo JSON must contain a sheets object.")
    patient_records = (
        payload["sheets"].get("Patient_Master", {}).get("records", [])
    )
    for patient in patient_records:
        patient["BMI"] = _calculate_bmi(
            patient.get("Height_cm"),
            patient.get("Weight_kg"),
        )
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


def assign_patient_payer(
    payload: dict[str, Any],
    patient_id: str,
    payer_name: str,
) -> dict[str, Any]:
    if payer_name not in PAYER_PLANS:
        raise ValueError("The only supported payer is Athena Health Insurance.")
    patient = _require_patient(payload, patient_id)
    insurance_sheet = payload["sheets"].get("Insurance")
    if not isinstance(insurance_sheet, dict) or not insurance_sheet.get("headers"):
        raise ValueError("Insurance is missing its column definitions.")

    records = insurance_sheet.setdefault("records", [])
    active_coverages = [
        row for row in records
        if row.get("Patient_ID") == patient_id and row.get("Status", "Active") == "Active"
    ]
    coverage = next(
        (row for row in active_coverages if row.get("Priority") == "Primary"),
        active_coverages[0] if active_coverages else None,
    )
    payer_code, plan_name = PAYER_PLANS[payer_name]
    if coverage is None:
        coverage = {header: "" for header in insurance_sheet["headers"]}
        coverage.update(
            {
                "Coverage_ID": (
                    "COV"
                    + str(
                        _next_numeric_suffix(
                            (str(row.get("Coverage_ID", "")) for row in records),
                            700001,
                        )
                    )
                ),
                "Patient_ID": patient_id,
                "Priority": "Primary",
                "Member_ID": f"{payer_code}-{patient_id.removeprefix('P')}",
                "Group_Number": f"{payer_code}-GRP-2026",
                "Relationship_to_Subscriber": "Self",
                "Effective_Date": date.today().isoformat(),
                "Status": "Active",
                "Assigned_PCP": patient.get("PCP", ""),
            }
        )
        records.append(coverage)

    for duplicate in active_coverages:
        if duplicate is not coverage:
            duplicate["Status"] = "Inactive"
    coverage.update(
        {
            "Priority": "Primary",
            "Payer_Name": payer_name,
            "Plan_Type": plan_name,
            "Member_ID": f"{payer_code}-{patient_id.removeprefix('P')}",
            "Group_Number": f"{payer_code}-GRP-2026",
            "Status": "Active",
        }
    )
    return coverage


def list_patients(path: Path | None = None) -> list[dict[str, Any]]:
    payload = load_ehr_data(path)
    patients = payload["sheets"].get("Patient_Master", {}).get("records", [])
    coverage_by_patient = {
        row.get("Patient_ID"): row
        for row in payload["sheets"].get("Insurance", {}).get("records", [])
        if row.get("Status", "Active") == "Active"
    }
    return [
        {
            **patient,
            "Payer_Name": coverage_by_patient.get(patient.get("Patient_ID"), {}).get("Payer_Name", ""),
            "Plan_Type": coverage_by_patient.get(patient.get("Patient_ID"), {}).get("Plan_Type", ""),
        }
        for patient in patients
    ]


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
    coverage = next(
        (
            row for row in payload["sheets"].get("Insurance", {}).get("records", [])
            if row.get("Patient_ID") == patient_id and row.get("Status", "Active") == "Active"
        ),
        {},
    )
    return {
        "patient": {
            **patient,
            "Payer_Name": coverage.get("Payer_Name", ""),
            "Plan_Type": coverage.get("Plan_Type", ""),
        },
        "records": records,
        "headers": headers,
    }


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
    payer_name = str(values.get("Payer_Name") or "Athena Health Insurance").strip()
    if payer_name and payer_name not in PAYER_PLANS:
        raise ValueError("The only supported payer is Athena Health Insurance.")
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
    patient["BMI"] = _calculate_bmi(
        patient.get("Height_cm"),
        patient.get("Weight_kg"),
    )
    sheet.setdefault("records", []).append(patient)
    if payer_name:
        payer_code, plan_name = PAYER_PLANS[payer_name]
        insurance_sheet = payload["sheets"].get("Insurance")
        if not isinstance(insurance_sheet, dict) or not insurance_sheet.get("headers"):
            raise ValueError("Insurance is missing its column definitions.")
        coverage = {header: "" for header in insurance_sheet["headers"]}
        coverage.update(
            {
                "Coverage_ID": f"COV{_next_numeric_suffix((str(row.get('Coverage_ID', '')) for row in insurance_sheet.get('records', [])), 700001)}",
                "Patient_ID": patient["Patient_ID"],
                "Priority": "Primary",
                "Payer_Name": payer_name,
                "Member_ID": f"{payer_code}-{patient['Patient_ID'].removeprefix('P')}",
                "Group_Number": f"{payer_code}-GRP-2026",
                "Relationship_to_Subscriber": "Self",
                "Effective_Date": date.today().isoformat(),
                "Status": "Active",
                "Plan_Type": plan_name,
                "Assigned_PCP": patient.get("PCP", ""),
            }
        )
        insurance_sheet.setdefault("records", []).append(coverage)
    save_ehr_data(payload, path)
    return {**patient, "Payer_Name": payer_name}


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
    before = dict(patient)
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
    patient["BMI"] = _calculate_bmi(
        patient.get("Height_cm"),
        patient.get("Weight_kg"),
    )
    if before != patient:
        _append_audit_log(
            payload,
            patient_id,
            "Patient demographics updated",
            "Patient_Master",
            before,
            dict(patient),
        )
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
    if sheet_name in {"Patient_Master", "Audit_Log"}:
        raise ValueError(f"Use the appropriate controlled endpoint to edit {sheet_name}.")

    headers = set(sheet.get("headers", []))
    before = [
        dict(record)
        for record in sheet.get("records", [])
        if record.get("Patient_ID") == patient_id
    ]
    normalized_records = []
    for record in records:
        normalized = {key: value for key, value in record.items() if key in headers}
        if sheet_name == "Insurance":
            payer_name = str(normalized.get("Payer_Name", "")).strip()
            if payer_name and payer_name not in PAYER_PLANS:
                raise ValueError("The only supported payer is Athena Health Insurance.")
            if (
                str(normalized.get("Status", "Active")).strip().lower() == "active"
                and payer_name != "Athena Health Insurance"
            ):
                raise ValueError("Active coverage must use Athena Health Insurance.")
            if payer_name == "Athena Health Insurance":
                normalized["Plan_Type"] = "Athena PPO"
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
    if before != normalized_records:
        _append_audit_log(
            payload,
            patient_id,
            "Patient records replaced",
            sheet_name,
            before,
            normalized_records,
        )
    save_ehr_data(payload, path)
    return normalized_records


def _append_audit_log(
    payload: dict[str, Any],
    patient_id: str,
    action: str,
    record_type: str,
    before: Any,
    after: Any,
) -> None:
    sheet = payload["sheets"].setdefault(
        "Audit_Log",
        {
            "structure": "records",
            "headers": AUDIT_LOG_HEADERS,
            "records": [],
        },
    )
    records = sheet.setdefault("records", [])
    records.append(
        {
            "Audit_ID": f"AUD-{uuid4().hex[:12].upper()}",
            "Patient_ID": patient_id,
            "Timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Actor": "Demo User",
            "Action": action,
            "Record_Type": record_type,
            "Before": json.dumps(before, ensure_ascii=False, sort_keys=True),
            "After": json.dumps(after, ensure_ascii=False, sort_keys=True),
        }
    )


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
    if sheet_name == "Audit_Log":
        raise ValueError("Audit_Log is append-only and cannot be edited directly.")

    headers = sheet.get("headers", [])
    record = {header: "" for header in headers}
    record.update({key: value for key, value in values.items() if key in headers and key != "Patient_ID"})
    if sheet_name == "Insurance":
        payer_name = str(record.get("Payer_Name", "")).strip()
        if payer_name and payer_name not in PAYER_PLANS:
            raise ValueError("The only supported payer is Athena Health Insurance.")
        record["Payer_Name"] = "Athena Health Insurance"
        record["Plan_Type"] = "Athena PPO"
        record["Priority"] = record.get("Priority") or "Primary"
        record["Status"] = record.get("Status") or "Active"
        record["Effective_Date"] = record.get("Effective_Date") or date.today().isoformat()
        record["Relationship_to_Subscriber"] = (
            record.get("Relationship_to_Subscriber") or "Self"
        )
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


def _calculate_bmi(height_cm: Any, weight_kg: Any) -> float | str:
    try:
        height = float(height_cm)
        weight = float(weight_kg)
    except (TypeError, ValueError):
        return ""
    if (
        not math.isfinite(height)
        or not math.isfinite(weight)
        or height <= 0
        or weight <= 0
    ):
        return ""
    return round(weight / ((height / 100) ** 2), 2)


def _age_on(birth_date: date, as_of: date) -> int:
    return as_of.year - birth_date.year - ((as_of.month, as_of.day) < (birth_date.month, birth_date.day))
