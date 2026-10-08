from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from uuid import uuid4

import streamlit as st

try:
    from cpt_coder import (
        classify_medical_item,
        get_cpt_codes,
        get_cpt_candidate_sets,
    )
    from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
except ModuleNotFoundError:
    try:
        from cpt_coder import (
            classify_medical_item,
            get_cpt_codes,
            get_cpt_candidate_sets,
        )
        from icd10_coder import get_icd10_candidate_sets, get_icd10_codes
    except ModuleNotFoundError:
        classify_medical_item = None
        get_cpt_candidate_sets = None
        get_cpt_codes = None
        get_icd10_candidate_sets = None
        get_icd10_codes = None

CODE_MATCHER_VERSION = "6"
SEED_DEMO_PATIENT_ID_MIN = 100001
SEED_DEMO_PATIENT_ID_MAX = 100050


MASTER_INSIGHTS = [
    "None", "Diabetes (Type 2)", "Hypertension", "Hyperlipidemia", "Asthma",
    "Osteoarthritis", "Obesity", "Smoking History", "Alcohol Use", "Anxiety",
    "Depression", "Fever", "Chronic Cough", "Fatigue", "Headache", "Chest Pain",
    "Nausea", "Color Blindness", "Complete Blood Count", "A1C Test", "Chest X-ray",
    "CT Scan", "MRI", "Ultrasound", "Blood Glucose Test",
]


def get_detected_insights(transcript_text):
    text = (transcript_text or "").lower()
    keyword_map = {
        "diabetes": "Diabetes (Type 2)", "hypertension": "Hypertension", "high bp": "Hypertension",
        "cholesterol": "Hyperlipidemia", "asthma": "Asthma", "arthritis": "Osteoarthritis",
        "obesity": "Obesity", "smok": "Smoking History", "alcohol": "Alcohol Use",
        "anxiety": "Anxiety", "depression": "Depression", "fever": "Fever",
        "cough": "Chronic Cough", "fatigue": "Fatigue", "headache": "Headache",
        "chest pain": "Chest Pain", "nausea": "Nausea", "color blind": "Color Blindness",
        "complete blood count": "Complete Blood Count", "cbc": "Complete Blood Count",
        "blood glucose": "Blood Glucose Test", "blood sugar": "Blood Glucose Test",
        "a1c": "A1C Test", "hba1c": "A1C Test", "chest x-ray": "Chest X-ray",
        "chest x ray": "Chest X-ray", "ct scan": "CT Scan", "mri": "MRI",
        "ultrasound": "Ultrasound",
        "physical therapy": "Physical Therapy", "physiotherapy": "Physical Therapy",
        "injection": "Injection", "injections": "Injection",
    }
    return list(dict.fromkeys(value for keyword, value in keyword_map.items() if keyword in text))


def get_record_insights(record):
    transcript_insights = get_detected_insights(record.get("transcript", ""))
    manual_insights = record.get("manual_insights", [])
    return list(dict.fromkeys(
        transcript_insights
        + [item for item in manual_insights if item != "None"]
        + ["Established Patient Office Visit"]
    ))


def code_results_signature(record, insights):
    return (
        str(record.get("id", "")),
        str(record.get("transcript", "")),
        tuple(insights),
        CODE_MATCHER_VERSION,
    )


def linked_patient_id_for_record(record, patient_lookup):
    patient_id = str(record.get("patient_id", "")).strip()
    patient = patient_lookup.get(patient_id)
    return patient_id if patient and patient_name_matches_record(record, patient) else ""


def patient_name_matches_record(record, patient):
    record_name = re.sub(r"[^a-z0-9]", "", str(record.get("name", "")).lower())
    patient_name = re.sub(
        r"[^a-z0-9]", "", str(patient.get("Legal_Name", "")).lower()
    )
    return bool(record_name and patient_name and record_name == patient_name)


def matching_patient_ids_for_record(record, patient_lookup):
    return [
        patient_id
        for patient_id, patient in patient_lookup.items()
        if patient_name_matches_record(record, patient)
    ]


def is_seed_demo_patient(patient_id):
    value = str(patient_id).strip()
    if not value.startswith("P") or not value[1:].isdigit():
        return False
    numeric_id = int(value[1:])
    return SEED_DEMO_PATIENT_ID_MIN <= numeric_id <= SEED_DEMO_PATIENT_ID_MAX


def billing_eligible_patients(patients):
    return [
        patient
        for patient in patients
        if not is_seed_demo_patient(patient.get("Patient_ID", ""))
    ]


def can_generate_bill(codes, services_confirmed, payer_supported, already_generated):
    return bool(codes) and services_confirmed and payer_supported and not already_generated


def revenue_leakage_warning(error):
    message = str(error).lower()
    if "rate" not in message and "unpriced" not in message:
        return ""
    return (
        "Revenue-leakage review: one or more documented codes have no matching "
        "Athena price. The bill was not saved as a partial bill. Review the "
        "missing code against the Athena-only charge source; do not treat an "
        "absent rate as a $0 service."
    )


def soap_sections(note_summary):
    text = str(note_summary or "")
    matches = list(
        re.finditer(
            r"(?im)^\s*(subjective|objective|assessment|plan)\s*:\s*",
            text,
        )
    )
    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).title()] = text[match.end():end].strip()
    return sections


def consultation_assessment(record):
    return soap_sections(record.get("summary", "")).get("Assessment", "")


def encounter_diagnosis_label(encounter, clinical_notes=()):
    for note in clinical_notes:
        if note.get("Encounter_ID") != encounter.get("Encounter_ID"):
            continue
        assessment = soap_sections(note.get("Note_Summary", "")).get("Assessment")
        if assessment:
            return assessment
    return encounter_diagnosis_label_from_fields(encounter)


def encounter_diagnosis_label_from_fields(encounter):
    narrative_markers = (
        "history of present illness:",
        "subjective:",
        "objective:",
        "assessment:",
        "plan:",
        "source transcript",
    )
    for field in ("Primary_Diagnosis", "Primary_Diagnosis_Code"):
        value = str(encounter.get(field) or "").strip()
        if (
            value
            and len(value) <= 300
            and not any(marker in value.lower() for marker in narrative_markers)
        ):
            return value
    return "Not recorded"


def find_matching_bill(bills, patient_id, encounter_id, payer_name, codes):
    requested_codes = {str(code).strip().upper() for code in codes if str(code).strip()}
    if not encounter_id or not requested_codes:
        return None
    for bill in reversed(bills):
        status = str(bill.get("Bill_Status", "")).strip().lower()
        if (
            bill.get("Patient_ID") != patient_id
            or bill.get("Encounter_ID") != encounter_id
            or bill.get("Payer_Name") != payer_name
            or bill.get("Superseded_By")
            or bill.get("Unpriced_Codes")
            or status in {"void", "cancelled", "canceled"}
        ):
            continue
        billed_codes = bill.get("CPT_HCPCS_Codes", [])
        if isinstance(billed_codes, str):
            billed_codes = re.split(r"[,;]", billed_codes)
        if {
            str(code).strip().upper()
            for code in billed_codes
            if str(code).strip()
        } == requested_codes:
            return bill
    return None


def matching_bill_exists(bills, patient_id, encounter_id, payer_name, codes):
    return find_matching_bill(
        bills, patient_id, encounter_id, payer_name, codes
    ) is not None


def append_audit_event(record, action, before, after, rationale=""):
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": "Demo User",
        "action": action,
        "before": deepcopy(before),
        "after": deepcopy(after),
        "rationale": rationale,
    }
    record.setdefault("audit_log", []).append(event)
    return event


def update_recommendation_review(record, decision, rationale=""):
    normalized_decision = str(decision).strip().lower()
    if normalized_decision not in {"accepted", "overridden"}:
        raise ValueError("Choose either accept or override for the recommendation.")
    reason = str(rationale).strip()
    if normalized_decision == "overridden" and not reason:
        raise ValueError("A rationale is required when overriding a recommendation.")

    previous = deepcopy(record.get("recommendation_review", {}))
    review = {
        "decision": normalized_decision,
        "rationale": reason,
        "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reviewer": "Demo User",
    }
    record["recommendation_review"] = review
    append_audit_event(
        record,
        f"CDS recommendation {normalized_decision}",
        previous,
        review,
        rationale=reason,
    )
    return review


def create_physician_query(
    record,
    patient_id,
    encounter_id,
    topic,
    question,
    clinical_indicators,
    response_options="",
):
    required_values = {
        "patient": patient_id,
        "encounter": encounter_id,
        "topic": topic,
        "question": question,
        "clinical indicators": clinical_indicators,
    }
    missing = [label for label, value in required_values.items() if not str(value).strip()]
    if missing:
        raise ValueError("Complete the physician query fields: " + ", ".join(missing) + ".")

    query = {
        "query_id": f"PQ-{uuid4().hex[:10].upper()}",
        "patient_id": str(patient_id),
        "encounter_id": str(encounter_id),
        "topic": str(topic).strip(),
        "question": str(question).strip(),
        "clinical_indicators": str(clinical_indicators).strip(),
        "response_options": str(response_options).strip(),
        "provider_response": "",
        "status": "Draft",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    queries = record.setdefault("physician_queries", [])
    queries.append(query)
    append_audit_event(
        record,
        "Physician query drafted",
        {"physician_queries": deepcopy(queries[:-1])},
        {"physician_queries": deepcopy(queries)},
    )
    return query


def record_physician_query_response(record, query_id, provider_response):
    response = str(provider_response).strip()
    if not response:
        raise ValueError("Enter the provider's response before saving it.")
    queries = record.get("physician_queries", [])
    query = next((item for item in queries if item.get("query_id") == query_id), None)
    if query is None:
        raise KeyError(query_id)
    before = deepcopy(query)
    query["provider_response"] = response
    query["status"] = "Answered"
    query["answered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    append_audit_event(
        record,
        "Physician query response recorded",
        before,
        query,
    )
    return query


def is_bill_history_visible(payer_name):
    payer = str(payer_name or "").strip()
    return not payer or payer == "Athena Health Insurance"


def validate_generated_bill(bill, patient_id, encounter_id, payer_name):
    errors = []
    if not bill:
        return ["Generate a bill before validating it."]
    if bill.get("Patient_ID") != patient_id:
        errors.append("The bill patient does not match the selected EHR patient.")
    if not bill.get("Encounter_ID"):
        errors.append("The bill has no encounter ID.")
    if encounter_id and bill.get("Encounter_ID") != encounter_id:
        errors.append("The bill encounter does not match the selected encounter.")
    if bill.get("Payer_Name") != payer_name:
        errors.append("The bill payer does not match the selected payer.")

    line_items = bill.get("Line_Items", [])
    if not line_items:
        errors.append("The bill has no priced line items.")
    if bill.get("Unpriced_Codes"):
        errors.append(
            "The bill contains unpriced codes: "
            + ", ".join(str(code) for code in bill["Unpriced_Codes"])
        )

    try:
        line_total = round(
            sum(float(line.get("Gross Charge (USD)", 0) or 0) for line in line_items),
            2,
        )
        bill_total = round(float(bill.get("Gross_Total_USD", 0) or 0), 2)
    except (TypeError, ValueError):
        errors.append("The bill contains a non-numeric charge amount.")
    else:
        if not math.isclose(line_total, bill_total, abs_tol=0.01):
            errors.append("The line-item gross charges do not equal the bill total.")

    for line in line_items:
        if line.get("Payer") != payer_name:
            errors.append("A bill line does not use the selected payer.")
            break
    return errors


def get_code_suggestions(insights, documentation=None):
    """Return separate ICD/CPT suggestions, using documentation context when supplied."""
    st.session_state.code_generation_error = ""

    if classify_medical_item is None or get_icd10_codes is None or get_cpt_codes is None:
        st.session_state.code_generation_error = (
            "code_matcher.py could not be imported. Keep code_matcher.py either in the "
            "frontend package or in the same directory as this Streamlit file."
        )
        return {"icd10": [], "cpt": []}

    candidates = list(dict.fromkeys(
        str(item).strip() for item in insights if item and item != "None"
    ))
    if not candidates:
        st.session_state.code_generation_error = "No codable clinical insights were detected."
        return {"icd10": [], "cpt": []}

    icd_items = []
    cpt_items = []
    classification_errors = []

    procedure_terms = (
        "test", "x-ray", "x ray", "scan", "mri", "ultrasound",
        "blood", "cbc", "a1c", "hba1c", "procedure", "office visit"
    )

    for item in candidates:
        try:
            item_type = str(classify_medical_item(item) or "").strip().lower()
        except Exception as error:
            classification_errors.append(f"{item}: {error}")
            item_type = ""

        if item_type in {"cpt", "hcpcs", "procedure"}:
            cpt_items.append(item)
        elif item_type in {"icd10", "icd-10", "icd", "diagnosis"}:
            icd_items.append(item)
        elif any(term in item.lower() for term in procedure_terms):
            cpt_items.append(item)
        else:
            # Diagnoses, symptoms, and risk factors should be evaluated for ICD-10.
            icd_items.append(item)

    try:
        documentation_text = str(documentation or "").lower()
        if "chest x-ray" in documentation_text or "chest x ray" in documentation_text:
            has_multiple_views = any(
                phrase in documentation_text
                for phrase in ("1-2 view", "1 to 2 view", "two view", "2 view")
            )
            if has_multiple_views:
                cpt_items = [
                    item for item in cpt_items
                    if "chest x-ray" not in item.lower() and "chest x ray" not in item.lower()
                ]
                cpt_items.append("Chest x ray 2 views")
        if documentation is not None and get_icd10_candidate_sets and get_cpt_candidate_sets:
            evidence = {item: documentation for item in icd_items}
            icd_details = get_icd10_candidate_sets(icd_items, threshold=80, evidence_by_term=evidence)
            icd_results = [
                {
                    "Extracted Condition": item["term"],
                    "Matched Disease/Injury": item["selected_description"],
                    "ICD-10 Code": item["selected_code"],
                }
                for item in icd_details
                if item["status"] == "suggested"
            ]
            # Keep procedure candidates visible for clinician review. Whether a
            # service was actually performed must be confirmed before billing.
            cpt_details = get_cpt_candidate_sets(cpt_items, documentation, threshold=80)
            cpt_results = [
                {
                    "Extracted Procedure": item["term"],
                    "Matched Procedure/Service": item["selected_description"],
                    "CPT/HCPCS Code": item["selected_code"],
                }
                for item in cpt_details
                if item["status"] == "suggested"
            ]
        else:
            icd_results = get_icd10_codes(icd_items, threshold=80) if icd_items else []
            cpt_results = get_cpt_codes(cpt_items, threshold=80) if cpt_items else []
    except Exception as error:
        st.session_state.code_generation_error = f"Code matching failed: {error}"
        return {"icd10": [], "cpt": []}

    if classification_errors:
        st.session_state.code_generation_error = (
            "Some items could not be classified automatically: "
            + "; ".join(classification_errors)
        )
    elif not icd_results and not cpt_results:
        st.session_state.code_generation_error = (
            "The matcher loaded correctly, but no entries met the 80% similarity threshold. "
            "Check the terms and code datasets loaded by code_matcher.py."
        )

    return {"icd10": icd_results or [], "cpt": cpt_results or []}


def get_backend_code_suggestions(insights, documentation=""):
    """Generate codes locally without an avoidable HTTP round trip."""
    return get_code_suggestions(insights, documentation)
