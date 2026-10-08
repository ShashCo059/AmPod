from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from threading import Lock
from typing import Any
from uuid import uuid4

import pandas as pd

from app.services.ehr_data_service import (
    WORKSPACE_ROOT,
    _append_audit_log,
    assign_patient_payer,
    load_ehr_data,
    save_ehr_data,
)


CHARGEMASTER_FILE = WORKSPACE_ROOT / "Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx"
ATHENA_CHARGES_FILE = WORKSPACE_ROOT / "Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json"
SUPPORTED_PAYERS = ("Athena Health Insurance",)
_BILL_WRITE_LOCK = Lock()
BILL_HEADERS = [
    "Bill_ID",
    "Patient_ID",
    "Patient_Name",
    "Payer_Name",
    "Encounter_ID",
    "Encounter_Type",
    "Generated_At",
    "Source_File",
    "Source_Sheet",
    "Hospital_Account",
    "Template_Hospital_Account",
    "Template_Encounter",
    "DRG",
    "ICD_10_Codes",
    "CPT_HCPCS_Codes",
    "Unpriced_Codes",
    "Line_Count",
    "Gross_Total_USD",
    "Expected_Allowed_Total_USD",
    "Patient_Responsibility_Total_USD",
    "Contractual_Adjustment_Total_USD",
    "Notice",
    "Bill_Status",
    "Superseded_By",
]
BILL_LINE_HEADERS = [
    "Bill_Line_ID",
    "Bill_ID",
    "Patient_ID",
    "Encounter_ID",
    "Service_Date",
    "Payer_Name",
    "Department",
    "Charge_Code",
    "Service",
    "Revenue_Code",
    "CPT_HCPCS",
    "Unit_Basis",
    "Quantity",
    "Unit_Charge_USD",
    "Gross_Charge_USD",
    "Expected_Allowed_USD",
    "Patient_Responsibility_USD",
    "Contractual_Adjustment_USD",
    "Line_Status",
    "Superseded_By",
]


class DuplicateBillError(ValueError):
    """Raised when a complete bill already exists for the same billing context."""


def generate_patient_bill(
    patient_id: str,
    icd10_codes: list[str] | None = None,
    cpt_hcpcs_codes: list[str] | None = None,
    encounter_id: str | None = None,
    encounter_type: str = "Outpatient",
    workbook_path: Path | None = None,
    data_path: Path | None = None,
    payer_name: str | None = None,
) -> dict[str, Any]:
    if payer_name is not None and payer_name not in SUPPORTED_PAYERS:
        raise ValueError("The only supported payer is Athena Health Insurance.")
    source = workbook_path or (
        ATHENA_CHARGES_FILE if payer_name else CHARGEMASTER_FILE
    )
    payload = load_ehr_data(data_path)
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

    encounter_sheet = payload["sheets"].get("Encounters", {})
    encounters = encounter_sheet.get("records", [])
    linked_encounter = next(
        (row for row in encounters if row.get("Encounter_ID") == encounter_id),
        None,
    ) if encounter_id else None
    if encounter_id and linked_encounter is None:
        raise KeyError(encounter_id)
    if linked_encounter and linked_encounter.get("Patient_ID") != patient_id:
        raise ValueError("The selected encounter does not belong to this patient.")

    is_inpatient = (
        "inpatient" in str(linked_encounter.get("Type", "")).lower()
        if linked_encounter
        else str(encounter_type).lower() == "inpatient"
    )
    normalized_encounter_type = "Inpatient" if is_inpatient else "Outpatient"
    pricing_sheet = "IPD Charges" if is_inpatient else "OPD Charges"
    charge_rows = _read_sheet(source, pricing_sheet)
    rates_by_code: dict[str, dict[str, Any]] = {}
    if payer_name:
        for row in charge_rows:
            code = _normalize_code(row.get("CPT/HCPCS"))
            if (
                not code
                or _text(row.get("Payer")) != payer_name
                or _text(row.get("Charge Status")).lower() not in ("", "posted")
            ):
                continue
            quantity = _decimal(row.get("Quantity"), default=Decimal("1"))
            if quantity <= 0:
                continue
            rates_by_code.setdefault(
                code,
                {
                    "Charge_Code": _text(row.get("Charge Code")),
                    "Service": _text(row.get("Charge Description")),
                    "Revenue_Code": _text(row.get("Revenue Code")),
                    "Unit_Basis": "Per unit",
                    "Department": _text(row.get("Department")),
                    "Unit_Rate": row.get("Unit Charge ($)"),
                    "Allowed_Rate": _decimal(row.get("Expected Allowed ($)")) / quantity,
                    "Patient_Rate": _athena_unit_rate(
                        row, "Patient Responsibility ($)", quantity
                    ),
                    "Adjustment_Rate": _athena_unit_rate(
                        row, "Contractual Adjustment ($)", quantity
                    ),
                    "Source": f"{source.name} · {pricing_sheet} · {payer_name}",
                },
            )
    else:
        chargemaster = _read_sheet(source, "CDM Master")
        for row in chargemaster:
            code = _normalize_code(row.get("CPT/HCPCS"))
            rate = row.get("Gross Unit Charge ($)")
            if code and rate is not None and not pd.isna(rate) and _text(row.get("Status")).lower() in ("", "active"):
                rates_by_code.setdefault(code, {
                    "Charge_Code": _text(row.get("EAP/CDM Charge Code")),
                    "Service": _text(row.get("Charge Description")),
                    "Revenue_Code": _text(row.get("Revenue Code")),
                    "Unit_Basis": _text(row.get("Unit Basis")),
                    "Department": _text(row.get("Department")),
                    "Unit_Rate": rate,
                    "Source": "CDM Master",
                })
        for row in charge_rows:
            code = _normalize_code(row.get("CPT/HCPCS"))
            rate = row.get("Unit Charge ($)")
            if code and rate is not None and not pd.isna(rate):
                rates_by_code.setdefault(code, {
                    "Charge_Code": _text(row.get("Charge Code")),
                    "Service": _text(row.get("Charge Description")),
                    "Revenue_Code": _text(row.get("Revenue Code")),
                    "Unit_Basis": "Per unit",
                    "Department": _text(row.get("Department")),
                    "Unit_Rate": rate,
                    "Source": pricing_sheet,
                })

    created_encounter = None
    if linked_encounter:
        patient_encounter_id = encounter_id
        service_date = _text(linked_encounter.get("Date"))[:10] or datetime.now().date().isoformat()
        normalized_encounter_type = "Inpatient" if "inpatient" in str(linked_encounter.get("Type", "")).lower() else "Outpatient"
    else:
        patient_encounter_id = f"ENC{_next_numeric_suffix(row.get('Encounter_ID', '') for row in encounters):05d}"
        service_date = datetime.now().date().isoformat()
        created_encounter = {header: "" for header in encounter_sheet.get("headers", [])}
        created_encounter.update(
            {
                "Encounter_ID": patient_encounter_id,
                "Patient_ID": patient_id,
                "Date": service_date,
                "Type": normalized_encounter_type,
                "Department": "Inpatient Facility" if is_inpatient else "Hospital Clinic",
                "Provider": patient.get("PCP", ""),
                "Facility": "Demo Regional Medical Center",
                "Status": "Billing Draft",
                "Primary_Diagnosis_Code": (icd10_codes or [""])[0],
                "Primary_Diagnosis": "; ".join(icd10_codes or []),
                "Priority": "Routine",
            }
        )

    prior_bills = payload["sheets"].get("Generated_Bills", {}).get("records", [])
    hospital_account = _text(linked_encounter.get("Hospital_Account")) if linked_encounter else ""
    if not hospital_account:
        hospital_account = next(
            (
                _text(existing_bill.get("Hospital_Account"))
                for existing_bill in reversed(prior_bills)
                if existing_bill.get("Patient_ID") == patient_id
                and existing_bill.get("Encounter_ID") == patient_encounter_id
                and _text(existing_bill.get("Hospital_Account"))
            ),
            "",
        )
    if not hospital_account:
        hospital_account = f"HAR-{uuid4().hex[:10].upper()}"
    if created_encounter is not None:
        created_encounter["Hospital_Account"] = hospital_account

    order_rows = [
        row for row in payload["sheets"].get("Orders_Procedures", {}).get("records", [])
        if row.get("Patient_ID") == patient_id and row.get("Encounter_ID") == patient_encounter_id
    ]
    quantities: dict[str, Decimal] = {}
    descriptions: dict[str, str] = {}
    for row in order_rows:
        code = _normalize_code(row.get("CPT_HCPCS"))
        if not code:
            continue
        quantities[code] = quantities.get(code, Decimal("0")) + _decimal(row.get("Quantity"), default=Decimal("1"))
        descriptions[code] = _text(row.get("Order_Description"))
    requested_codes = list(dict.fromkeys(_normalize_code(code) for code in (cpt_hcpcs_codes or []) if _normalize_code(code)))
    for code in requested_codes:
        quantities.setdefault(code, Decimal("1"))

    if not quantities:
        raise ValueError("This patient encounter has no CPT/HCPCS codes or procedures to bill.")

    bill_id = f"BILL-{uuid4().hex[:12].upper()}"
    line_items = []
    gross_total = Decimal("0")
    allowed_total = Decimal("0")
    patient_total = Decimal("0")
    adjustment_total = Decimal("0")
    unpriced_codes = []
    for code, quantity in quantities.items():
        charge = rates_by_code.get(code)
        if not charge:
            unpriced_codes.append(code)
            continue
        unit_rate = _decimal(charge["Unit_Rate"])
        line_total = _money(unit_rate * quantity)
        gross_total += line_total
        line = {
            "Bill_Line_ID": f"{bill_id}-L{len(line_items) + 1:03d}",
            "Bill_ID": bill_id,
            "Patient_ID": patient_id,
            "Encounter_ID": patient_encounter_id,
            "Service Date": service_date,
            "Department": charge["Department"],
            "Charge Code": charge["Charge_Code"],
            "Service": descriptions.get(code) or charge["Service"] or code,
            "Revenue Code": charge["Revenue_Code"],
            "CPT/HCPCS": code,
            "Unit Basis": charge["Unit_Basis"],
            "Quantity": _number(quantity),
            "Unit Charge (USD)": _number(unit_rate),
            "Gross Charge (USD)": _number(line_total),
        }
        if payer_name:
            allowed_line = _money(charge["Allowed_Rate"] * quantity)
            patient_line = _money(charge["Patient_Rate"] * quantity)
            adjustment_line = _money(charge["Adjustment_Rate"] * quantity)
            allowed_total += allowed_line
            patient_total += patient_line
            adjustment_total += adjustment_line
            line.update(
                {
                    "Payer": payer_name,
                    "Expected Allowed (USD)": _number(allowed_line),
                    "Patient Responsibility (USD)": _number(patient_line),
                    "Contractual Adjustment (USD)": _number(adjustment_line),
                }
            )
        line_items.append(line)

    if unpriced_codes:
        raise ValueError(
            f"Bill was not generated because these CPT/HCPCS codes have no "
            f"{payer_name or 'chargemaster'} rate for {pricing_sheet}: "
            f"{', '.join(unpriced_codes)}. Add verified rates before billing."
        )
    if not line_items:
        raise ValueError(f"No requested or encounter-linked codes are priced in the {pricing_sheet} chargemaster.")

    source_sheets = list(dict.fromkeys(charge["Source"] for code in quantities if (charge := rates_by_code.get(code))))
    source_drg = _text(linked_encounter.get("DRG")) if linked_encounter else ""
    notice = (
        f"Synthetic {payer_name} allowed and patient-share estimates from "
        f"{source.name}, {pricing_sheet}; these are not adjudicated amounts."
        if payer_name
        else "Gross charges from this patient's encounter codes and the original chargemaster; no payer or patient-share allocation."
    )
    return {
        "Bill_ID": bill_id,
        "Patient_ID": patient_id,
        "Patient_Name": patient.get("Legal_Name", ""),
        "Payer_Name": payer_name or "",
        "Encounter_ID": patient_encounter_id,
        "Encounter_Type": normalized_encounter_type,
        "Generated_At": datetime.now().isoformat(timespec="seconds"),
        "Source_File": source.name,
        "Source_Sheet": " + ".join(source_sheets),
        "Hospital_Account": hospital_account,
        "Template_Hospital_Account": "",
        "Template_Encounter": "",
        "DRG": source_drg,
        "ICD_10_Codes": list(dict.fromkeys(icd10_codes or [])),
        "CPT_HCPCS_Codes": list(quantities),
        "Unpriced_Codes": unpriced_codes,
        "Line_Count": len(line_items),
        "Line_Items": line_items,
        "Gross_Total_USD": _number(gross_total),
        "Expected_Allowed_Total_USD": _number(allowed_total) if payer_name else "",
        "Patient_Responsibility_Total_USD": _number(patient_total) if payer_name else "",
        "Contractual_Adjustment_Total_USD": _number(adjustment_total) if payer_name else "",
        "Notice": notice,
        "_Encounter_Record": created_encounter,
    }


def save_generated_bill(bill: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    with _BILL_WRITE_LOCK:
        return _save_generated_bill(bill, path)


def _save_generated_bill(bill: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    payload = load_ehr_data(path)
    patient_id = str(bill.get("Patient_ID", ""))
    if not any(
        row.get("Patient_ID") == patient_id
        for row in payload["sheets"].get("Patient_Master", {}).get("records", [])
    ):
        raise KeyError(patient_id)
    encounter_id = _text(bill.get("Encounter_ID"))
    payer_name = _text(bill.get("Payer_Name"))
    codes = {_normalize_code(code) for code in bill.get("CPT_HCPCS_Codes", [])}
    if encounter_id and codes:
        for existing_bill in payload["sheets"].get("Generated_Bills", {}).get("records", []):
            status = _text(existing_bill.get("Bill_Status")).lower()
            if (
                existing_bill.get("Patient_ID") != patient_id
                or _text(existing_bill.get("Encounter_ID")) != encounter_id
                or _text(existing_bill.get("Payer_Name")) != payer_name
                or existing_bill.get("Superseded_By")
                or existing_bill.get("Unpriced_Codes")
                or status in {"void", "cancelled", "canceled", "superseded"}
            ):
                continue
            existing_codes = {
                _normalize_code(code)
                for code in existing_bill.get("CPT_HCPCS_Codes", [])
            }
            if existing_codes == codes:
                raise DuplicateBillError(
                    "A complete bill already exists for this patient, encounter, "
                    "payer, and CPT/HCPCS code set."
                )
    encounter_record = bill.get("_Encounter_Record")
    summary = {key: bill.get(key, "") for key in BILL_HEADERS}
    summary["Bill_Status"] = "Draft"
    if payer_name:
        assign_patient_payer(payload, patient_id, payer_name)
    if encounter_record:
        payload["sheets"]["Encounters"].setdefault("records", []).append(encounter_record)
    else:
        linked_encounter = next(
            (
                row
                for row in payload["sheets"].get("Encounters", {}).get("records", [])
                if row.get("Encounter_ID") == bill.get("Encounter_ID")
                and row.get("Patient_ID") == patient_id
            ),
            None,
        )
        if linked_encounter is None:
            raise KeyError(str(bill.get("Encounter_ID", "")))
        if not _text(linked_encounter.get("Hospital_Account")):
            linked_encounter["Hospital_Account"] = bill.get("Hospital_Account", "")

    sheet = payload["sheets"].setdefault(
        "Generated_Bills",
        {"structure": "records", "headers": BILL_HEADERS, "records": []},
    )
    sheet.setdefault("records", [])
    legacy_lines = []
    for existing_bill in sheet["records"]:
        existing_bill.setdefault("Template_Hospital_Account", existing_bill.get("Hospital_Account", ""))
        existing_bill.setdefault("Template_Encounter", existing_bill.get("Source_Encounter", ""))
        existing_bill.setdefault("Encounter_ID", "")
        existing_bill.setdefault("Line_Count", len(existing_bill.get("Line_Items", [])))
        if not existing_bill.get("Bill_ID"):
            continue
        for index, line in enumerate(existing_bill.get("Line_Items", []), start=1):
            line_id = f"{existing_bill['Bill_ID']}-L{index:03d}"
            if not any(
                saved.get("Bill_Line_ID") == line_id
                for saved in payload["sheets"].get("Generated_Bill_Lines", {}).get("records", [])
            ):
                legacy_lines.append(_normalized_line(existing_bill, line, line_id))
    sheet["headers"] = BILL_HEADERS
    sheet.setdefault("records", []).append(summary)
    line_sheet = payload["sheets"].setdefault(
        "Generated_Bill_Lines",
        {"structure": "records", "headers": BILL_LINE_HEADERS, "records": []},
    )
    line_sheet.setdefault("records", []).extend(legacy_lines)
    line_sheet["headers"] = BILL_LINE_HEADERS
    _supersede_template_bills(payload, patient_id, bill["Bill_ID"])
    for line in bill.get("Line_Items", []):
        line_sheet["records"].append(_normalized_line(bill, line, line["Bill_Line_ID"]))
    claims_sheet = payload["sheets"].get("Billing_Claims")
    if isinstance(claims_sheet, dict) and claims_sheet.get("structure") == "records":
        claim_headers = claims_sheet.get("headers", [])
        claim_records = claims_sheet.setdefault("records", [])
        for index, line in enumerate(bill.get("Line_Items", []), start=1):
            claim = {header: "" for header in claim_headers}
            claim.update(
                {
                    "Claim_ID": f"CLM-{bill['Bill_ID'].removeprefix('BILL-')}-{index:03d}",
                    "Patient_ID": patient_id,
                    "Encounter_ID": bill["Encounter_ID"],
                    "Service_Date": line["Service Date"],
                    "CPT_HCPCS": line["CPT/HCPCS"],
                    "Description": line["Service"],
                    "Units": line["Quantity"],
                    "Charge_USD": line["Gross Charge (USD)"],
                    "Claim_Status": "Draft",
                    "Currency": "USD",
                    "Payer": bill.get("Payer_Name", ""),
                    "Expected_Allowed_USD": line.get("Expected Allowed (USD)", ""),
                    "Contractual_Adjustment_USD": line.get("Contractual Adjustment (USD)", ""),
                    "Patient_Balance_USD": line.get("Patient Responsibility (USD)", ""),
                }
            )
            claim_records.append(claim)
    _append_audit_log(
        payload,
        patient_id,
        "Draft bill generated",
        "Generated_Bills",
        {},
        {
            "Bill_ID": bill.get("Bill_ID", ""),
            "Encounter_ID": bill.get("Encounter_ID", ""),
            "Payer_Name": payer_name,
            "CPT_HCPCS_Codes": bill.get("CPT_HCPCS_Codes", []),
            "Gross_Total_USD": bill.get("Gross_Total_USD", ""),
        },
    )
    workbook = payload.setdefault("workbook", {})
    sheet_order = workbook.setdefault("sheet_order", list(payload["sheets"]))
    for generated_sheet in ("Generated_Bills", "Generated_Bill_Lines"):
        if generated_sheet not in sheet_order:
            sheet_order.append(generated_sheet)
    workbook["sheet_count"] = len(sheet_order)
    save_ehr_data(payload, path)
    return {key: value for key, value in bill.items() if key != "_Encounter_Record"}


def supersede_patient_template_bills(
    patient_id: str,
    replacement_bill_id: str,
    path: Path | None = None,
) -> dict[str, int]:
    payload = load_ehr_data(path)
    replacement = next(
        (
            bill for bill in payload["sheets"].get("Generated_Bills", {}).get("records", [])
            if bill.get("Bill_ID") == replacement_bill_id and bill.get("Patient_ID") == patient_id
        ),
        None,
    )
    if replacement is None:
        raise KeyError(replacement_bill_id)
    replacement["Bill_Status"] = "Draft"
    replacement.setdefault("Superseded_By", "")
    counts = _supersede_template_bills(payload, patient_id, replacement_bill_id)
    save_ehr_data(payload, path)
    return counts


def _read_sheet(source: Path, sheet_name: str) -> list[dict[str, Any]]:
    if source.suffix.lower() == ".json":
        try:
            with source.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read charge data JSON at {source}.") from error
        sheets = payload.get("sheets") if isinstance(payload, dict) else None
        sheet = sheets.get(sheet_name) if isinstance(sheets, dict) else None
        records = sheet.get("records") if isinstance(sheet, dict) else None
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise ValueError(
                f"Charge data JSON at {source} is missing valid records for "
                f"'{sheet_name}'."
            )
        return records
    frame = pd.read_excel(source, sheet_name=sheet_name, header=3, dtype=object)
    frame = frame.dropna(how="all")
    return frame.to_dict(orient="records")


def _athena_unit_rate(
    row: dict[str, Any], field_name: str, quantity: Decimal
) -> Decimal:
    value = row.get(field_name)
    if isinstance(value, str) and value.startswith("="):
        if field_name == "Patient Responsibility ($)":
            match = re.fullmatch(r"=P\d+\*([0-9]+(?:\.[0-9]+)?)%", value)
            if match:
                allowed = _decimal(row.get("Expected Allowed ($)"))
                percentage = Decimal(match.group(1)) / Decimal("100")
                return _money(allowed * percentage / quantity)
        elif field_name == "Contractual Adjustment ($)" and re.fullmatch(
            r"=O\d+-P\d+", value
        ):
            gross = _decimal(row.get("Unit Charge ($)")) * quantity
            allowed = _decimal(row.get("Expected Allowed ($)"))
            return _money((gross - allowed) / quantity)
        raise ValueError(
            f"Unsupported charge formula in Athena rate field "
            f"'{field_name}': {value}"
        )
    return _decimal(value) / quantity


def _decimal(value: Any, default: Decimal | None = None) -> Decimal:
    if value is None or pd.isna(value) or str(value).strip() == "":
        if default is not None:
            return default
        raise ValueError("A gross unit rate is missing from the chargemaster.")
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation as error:
        raise ValueError(f"Invalid numeric charge value: {value}") from error


def _text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_code(value: Any) -> str:
    return "".join(_text(value).upper().split())


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def _next_numeric_suffix(values) -> int:
    suffixes = []
    for value in values:
        match = re.search(r"(\d+)$", str(value))
        if match:
            suffixes.append(int(match.group(1)))
    return max(suffixes, default=0) + 1


def _normalized_line(bill: dict[str, Any], line: dict[str, Any], line_id: str) -> dict[str, Any]:
    return {
        "Bill_Line_ID": line_id,
        "Bill_ID": bill.get("Bill_ID", ""),
        "Patient_ID": bill.get("Patient_ID", ""),
        "Encounter_ID": bill.get("Encounter_ID", ""),
        "Service_Date": line.get("Service Date", ""),
        "Payer_Name": line.get("Payer", bill.get("Payer_Name", "")),
        "Department": line.get("Department", ""),
        "Charge_Code": line.get("Charge Code", ""),
        "Service": line.get("Service", ""),
        "Revenue_Code": line.get("Revenue Code", ""),
        "CPT_HCPCS": line.get("CPT/HCPCS", ""),
        "Unit_Basis": line.get("Unit Basis", ""),
        "Quantity": line.get("Quantity", ""),
        "Unit_Charge_USD": line.get("Unit Charge (USD)", ""),
        "Gross_Charge_USD": line.get("Gross Charge (USD)", ""),
        "Expected_Allowed_USD": line.get("Expected Allowed (USD)", ""),
        "Patient_Responsibility_USD": line.get("Patient Responsibility (USD)", ""),
        "Contractual_Adjustment_USD": line.get("Contractual Adjustment (USD)", ""),
        "Line_Status": "Draft",
        "Superseded_By": "",
    }


def _supersede_template_bills(
    payload: dict[str, Any], patient_id: str, replacement_bill_id: str
) -> dict[str, int]:
    encounters = {
        row.get("Encounter_ID"): row
        for row in payload["sheets"].get("Encounters", {}).get("records", [])
    }
    bills = payload["sheets"].get("Generated_Bills", {}).get("records", [])
    replacement = next((bill for bill in bills if bill.get("Bill_ID") == replacement_bill_id), {})
    old_bill_ids = set()
    for bill in bills:
        if bill.get("Patient_ID") != patient_id or bill.get("Bill_ID") == replacement_bill_id:
            continue
        encounter = encounters.get(bill.get("Encounter_ID"), {})
        is_template = (
            "ipd encounter" in str(bill.get("Source_Sheet", "")).lower()
            and "reference billing" in str(encounter.get("Type", "")).lower()
        )
        if not is_template or bill.get("Bill_Status", "Draft") != "Draft":
            continue
        bill["Bill_Status"] = "Superseded"
        bill["Superseded_By"] = replacement_bill_id
        old_bill_ids.add(bill.get("Bill_ID"))
        encounter["Status"] = "Superseded"

    voided_line_count = 0
    for line in payload["sheets"].get("Generated_Bill_Lines", {}).get("records", []):
        if line.get("Patient_ID") == patient_id and line.get("Bill_ID") in old_bill_ids:
            line["Line_Status"] = "Voided"
            line["Superseded_By"] = replacement_bill_id
            voided_line_count += 1
    voided_claim_count = 0
    for claim in payload["sheets"].get("Billing_Claims", {}).get("records", []):
        claim_id = str(claim.get("Claim_ID", ""))
        matching_bill_id = next(
            (
                bill_id for bill_id in old_bill_ids
                if claim_id.startswith(f"CLM-{str(bill_id).removeprefix('BILL-')}-")
            ),
            None,
        )
        if (
            claim.get("Patient_ID") == patient_id
            and matching_bill_id
            and claim.get("Claim_Status") == "Draft"
        ):
            claim["Claim_Status"] = "Void"
            claim["Notes"] = f"Superseded by corrected patient bill {replacement_bill_id}."
            voided_claim_count += 1
    return {
        "bill_count": len(old_bill_ids),
        "line_count": voided_line_count,
        "claim_count": voided_claim_count,
    }
