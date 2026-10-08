import io
import json
import math
import re
import sys
import uuid
from datetime import datetime
from html import escape, unescape
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import altair as alt
import pandas as pd
import requests
import speech_recognition as sr
import streamlit as st

from frontend.ui_theme import apply_theme
from frontend.record_store import load_records, save_records
from frontend.clinical_workflow import (
    CODE_MATCHER_VERSION,
    MASTER_INSIGHTS,
    append_audit_event,
    billing_eligible_patients,
    can_generate_bill,
    code_results_signature,
    create_physician_query,
    consultation_assessment,
    encounter_diagnosis_label,
    find_matching_bill,
    get_backend_code_suggestions,
    get_code_suggestions,
    get_detected_insights,
    get_record_insights,
    is_bill_history_visible,
    is_seed_demo_patient,
    linked_patient_id_for_record,
    matching_patient_ids_for_record,
    patient_name_matches_record,
    record_physician_query_response,
    revenue_leakage_warning,
    soap_sections,
    update_recommendation_review,
    validate_generated_bill,
)

if st.session_state.get("theme_defaults_version") != "light-default-v3":
    st.session_state.theme = "Light"
    st.session_state.theme_defaults_version = "light-default-v3"
theme_name = st.session_state.get("theme", "Light")

try:
    from frontend.api_client import HealthcareApiClient
except ModuleNotFoundError:
    from api_client import HealthcareApiClient

# ============================================================
# 1. CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="NuuCare | Clinical Scribe",
    page_icon="AP",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items=None,
)

DB_FILE = PROJECT_ROOT / "clinical_records.json"
ANALYSIS_VERSION = "2"
SUPPORTED_PAYER_OPTIONS = ["Athena Health Insurance"]


# ============================================================
# 2. THEME AND LAYOUT
# ============================================================
apply_theme(theme_name)


# ============================================================
# 3. DATABASE AND SESSION STATE
# ============================================================
def load_db():
    return load_records(DB_FILE)


def save_db(records):
    save_records(DB_FILE, records)


def initialize_state():
    defaults = {
        "records": None,
        "page": "dashboard",
        "current_record": None,
        "dash_page": 1,
        "chart_cases_page": 1,
        "scribe_result": None,
        "insight_count": 1,
        "detected_insights": [],
        "last_record_id": None,
        "edit_summary": "",
        "code_results": {"icd10": [], "cpt": []},
        "code_record_id": None,
        "code_results_signature": None,
        "code_matcher_version": None,
        "code_generation_error": "",
        "generated_bill": None,
        "generated_bill_record_id": None,
        "generated_bill_patient_id": None,
        "generated_bill_signature": None,
        "bill_validation_signature": None,
        "bill_validation_errors": None,
    }
    if "records" not in st.session_state:
        try:
            st.session_state.records = load_db()
        except RuntimeError as error:
            st.error(
                f"{error} No consultation data was changed. Repair the file before "
                "continuing so the app cannot overwrite it with an empty record set."
            )
            st.stop()
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


initialize_state()

# ============================================================
# 4. REUSABLE COMPONENTS
# ============================================================
def go_to(page):
    if page == "codes" or page != st.session_state.page:
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None
    st.session_state.page = page


def start_new_consultation():
    st.session_state.scribe_result = None
    st.session_state.page = "new_consult"


def open_record(record, destination="review"):
    st.session_state.current_record = record
    st.session_state.detected_insights = get_record_insights(record)
    st.session_state.insight_count = 1
    go_to(destination)


def render_sidebar(active_page):
    with st.sidebar:
        st.markdown(
            '<div class="shell-logo">NuuCare</div>'
            '<div class="shell-subtitle">Clinical Intelligence Platform</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="shell-section-label">WORKSPACE</div>', unsafe_allow_html=True)
        navigation = [
            ("dashboard", "Overview"),
            ("new_consult", "Consultations"),
            ("ehr_data", "EHR Demo Data"),
            ("review", "Clinical Review"),
            ("codes", "Codes"),
        ]
        for page_key, label in navigation:
            if st.button(label, key=f"nav_{page_key}", use_container_width=True):
                if page_key == "review" and not st.session_state.current_record:
                    go_to("dashboard")
                else:
                    go_to(page_key)
                st.rerun()
            if active_page == page_key:
                st.markdown('<div class="nav-active-marker"></div>', unsafe_allow_html=True)

        try:
            directory_patients = HealthcareApiClient().list_patients()
        except (RuntimeError, requests.exceptions.RequestException) as error:
            directory_patients = []
            st.warning(f"Patient directory is unavailable: {error}")
        if directory_patients:
            directory_by_id = {
                str(patient.get("Patient_ID", "")): patient
                for patient in directory_patients
                if patient.get("Patient_ID")
            }
            directory_ids = list(directory_by_id)

            def open_directory_patient():
                patient_id = st.session_state.get("sidebar_patient_id", "")
                patient = directory_by_id.get(patient_id)
                if patient:
                    st.session_state.page = "ehr_data"
                    st.session_state.ehr_demo_patient = (
                        f"{patient_id} · {patient.get('Legal_Name', 'Unknown patient')}"
                    )

            if st.session_state.get("sidebar_patient_id") not in directory_by_id:
                st.session_state.sidebar_patient_id = directory_ids[0]
            with st.expander(f"Patient directory · {len(directory_ids)}", expanded=False):
                st.selectbox(
                    "Patient and insurance payer",
                    directory_ids,
                    key="sidebar_patient_id",
                    format_func=lambda patient_id: (
                        f"{directory_by_id[patient_id].get('Legal_Name', patient_id)} · "
                        f"{directory_by_id[patient_id].get('Payer_Name') or 'Payer not assigned'}"
                    ),
                    on_change=open_directory_patient,
                    label_visibility="collapsed",
                )

        st.markdown('<div class="shell-divider"></div><div class="shell-section-label">UTILITY</div>', unsafe_allow_html=True)
        selected_theme = st.selectbox("Theme", ["Light", "Dark"], key="theme_selector_v2")
        if st.session_state.get("theme") != selected_theme:
            st.session_state.theme = selected_theme
            st.rerun()
        with st.expander("Help & safety", expanded=False):
            st.caption(
                "Educational demo only. Do not enter real patient information. "
                "Consultation audio is processed by Google Speech Recognition, "
                "and transcripts are sent to the configured backend and model."
            )
            st.markdown(
                "Use **Consultations** to create a note, **Clinical Review** to "
                "review it, and **Codes** to inspect coding and bill estimates."
            )
        st.markdown(
            '<div class="shell-user"><div class="user-avatar">DE</div>'
            '<div><b>Demo User</b><small>Clinical Operations</small></div></div>',
            unsafe_allow_html=True,
        )


def render_header(title, subtitle, show_new=True):
    left, action = st.columns([8, 2], gap="medium", vertical_alignment="center")
    left.markdown(
        f'<div class="page-heading">{escape(title)}</div>'
        f'<div class="page-kicker">{escape(subtitle)}</div>',
        unsafe_allow_html=True,
    )
    if show_new and action.button("New Consultation", key=f"header_new_{st.session_state.page}", use_container_width=True, type="primary"):
        start_new_consultation()
        st.rerun()


def render_kpi_card(label, value, accent, context):
    st.markdown(
        f'<div class="kpi-card" style="border-top-color:{accent}">'
        f'<div class="kpi-title">{escape(label)}</div><div class="kpi-value">{value}</div>'
        f'<div class="kpi-context">{escape(context)}</div></div>', unsafe_allow_html=True,
    )


def render_status_badge(status):
    css_class = "status-approved" if status == "Approved" else "status-pending"
    return f'<span class="status-pill {css_class}">{escape(status.upper())}</span>'


def parse_transcript_entries(transcript, doctor_name="Doctor", patient_name="Patient"):
    text = (transcript or "").strip()
    if not text:
        return []
    entries, current_speaker, current_text = [], doctor_name, ""

    def flush():
        nonlocal current_text
        if current_text.strip():
            entries.append({"Speaker": current_speaker, "Text": current_text.strip()})
        current_text = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(doctor|patient)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if match:
            flush()
            current_speaker = doctor_name if match.group(1).lower() == "doctor" else patient_name
            current_text = match.group(2).strip()
        else:
            current_text = f"{current_text} {line}".strip()
    flush()
    return entries or [{"Speaker": doctor_name, "Text": text}]


def append_selected_insights_to_note(current_text, selected_insights):
    updated = current_text or ""
    values = [value for value in selected_insights if value and value != "None"]
    if not values:
        return updated
    additions = "\n".join(f"• {value}" for value in values)
    heading = "MAJOR DIAGNOSIS / ISSUES"
    if "Awaiting manual input." in updated:
        return updated.replace("Awaiting manual input.", additions, 1)
    if heading in updated:
        return f"{updated.rstrip()}\n{additions}"
    return f"{updated.rstrip()}\n\n{heading}\n{additions}".strip()


def render_patient_table(records, selected_date=None):
    if selected_date:
        records = [r for r in records if selected_date in (r.get("date"), r.get("approval_date"))]
        st.caption(f"Cases for {selected_date} ({len(records)})")
    per_page = 3 if selected_date else 5
    state_key = "chart_cases_page" if selected_date else "dash_page"
    total_pages = max(1, math.ceil(len(records) / per_page))
    st.session_state[state_key] = min(max(st.session_state[state_key], 1), total_pages)
    current_page = st.session_state[state_key]
    current_records = records[(current_page - 1) * per_page: current_page * per_page]
    st.markdown(
        '<div class="patient-table-header"><span>Patient</span><span>ID</span><span>Doctor</span>'
        '<span>Date</span><span>Status</span><span>Action</span></div>', unsafe_allow_html=True,
    )
    for row_index, record in enumerate(current_records):
        columns = st.columns([2.2, 1.15, 1.7, 1.55, 1.05, 1.15], vertical_alignment="center")
        columns[0].markdown(f'<div class="patient-cell"><b>{escape(record.get("name", ""))}</b><small>{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))}</small></div>', unsafe_allow_html=True)
        columns[1].markdown(f'<span class="table-meta">{escape(record.get("id", ""))}</span>', unsafe_allow_html=True)
        columns[2].markdown(f'<span class="table-meta">{escape(record.get("doctor", "N/A"))}</span>', unsafe_allow_html=True)
        date_text = record.get("approval_date") if record.get("status") == "Approved" else record.get("date", "")
        columns[3].markdown(f'<div class="table-two-line"><span>{escape(date_text or "")}</span><small>{escape(record.get("time", ""))}</small></div>', unsafe_allow_html=True)
        columns[4].markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
        key_suffix = f'{record.get("id", "record")}_{row_index}_{current_page}'
        if columns[5].button("View" if record.get("status") == "Approved" else "Review", key=f"record_{key_suffix}", use_container_width=True):
            open_record(record)
            st.rerun()
        st.markdown('<div class="table-row-rule"></div>', unsafe_allow_html=True)
    if total_pages > 1:
        previous, info, next_col = st.columns([1, 2, 1], vertical_alignment="center")
        if previous.button("Previous", key=f"previous_{state_key}", disabled=current_page == 1, use_container_width=True):
            st.session_state[state_key] -= 1
            st.rerun()
        info.markdown(f'<div class="pagination-label">Page {current_page} of {total_pages}</div>', unsafe_allow_html=True)
        if next_col.button("Next", key=f"next_{state_key}", disabled=current_page == total_pages, use_container_width=True):
            st.session_state[state_key] += 1
            st.rerun()


def get_insight_category(insight):
    lowered = insight.lower()
    if any(x in lowered for x in ("diabetes", "hypertension", "cholesterol", "asthma", "arthritis")):
        return "Diagnoses", "insight-diagnosis"
    if any(x in lowered for x in ("smok", "alcohol", "obesity")):
        return "Risk Factors", "insight-risk"
    if any(x in lowered for x in ("x-ray", "ct scan", "blood", "cbc", "a1c", "mri", "ultrasound")):
        return "Tests/Procedures", "insight-procedure"
    return "Symptoms", "insight-symptom"


def render_grouped_insights(insights):
    grouped = {category: [] for category in ("Diagnoses", "Symptoms", "Risk Factors", "Tests/Procedures")}
    for insight in insights:
        category, css_class = get_insight_category(insight)
        grouped[category].append((insight, css_class))
    parts = ['<div class="insight-scroll">']
    for category, values in grouped.items():
        if not values:
            continue
        parts.append(f'<div class="insight-category"><div class="insight-category-heading">{escape(category)} ({len(values)})</div><div class="insight-category-items">')
        for insight, css_class in values:
            parts.append(f'<div class="insight-chip {css_class}"><b>{escape(insight)}</b></div>')
        parts.append("</div></div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render_clinical_graph(patient_name, insights):
    categories = {"Diagnoses": [], "Symptoms": [], "Risk Factors": []}
    for insight in insights:
        category, _ = get_insight_category(insight)
        categories[category if category in categories else "Symptoms"].append(insight)
    class_map = {"Diagnoses": "diagnosis", "Symptoms": "symptom", "Risk Factors": "risk"}
    parts = ['<div class="clinical-graph-v2"><div class="graph-hierarchy-v2">', f'<div class="graph-patient-card">Patient: {escape(patient_name[:40])}</div>', '<div class="graph-connector-main"></div><div class="graph-categories">']
    for category, values in categories.items():
        parts.append(f'<div><div class="graph-category-header">{category}</div><div class="graph-category-content">')
        if values:
            for value in values:
                parts.append(f'<div class="graph-node-card graph-node-{class_map[category]}">{escape(value)}</div>')
        else:
            parts.append(f'<div class="graph-empty-state">No {category.lower()} detected</div>')
        parts.append("</div></div>")
    parts.append("</div></div></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def clean_clinical_text(value):
    text = unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def render_summary(summary):
    text = clean_clinical_text(summary)
    if not text:
        st.caption("No clinical summary is available.")
        return
    headings = {"subjective", "objective", "chief complaint", "history of present illness", "past medical history", "current medications", "assessment", "plan"}
    fields, current_heading, current_lines = [], None, []
    for line in text.splitlines():
        inline_heading = re.match(r"^\s*(Subjective|Objective|Assessment|Plan)\s*:\s*(.*)$", line, re.IGNORECASE)
        if inline_heading:
            if current_heading:
                fields.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = inline_heading.group(1)
            current_lines = [inline_heading.group(2)] if inline_heading.group(2) else []
            continue
        stripped = line.strip().strip("#*- ").rstrip(":")
        if stripped.lower() in headings:
            if current_heading and current_lines:
                fields.append((current_heading, "\n".join(current_lines).strip()))
            current_heading, current_lines = stripped, []
        elif current_heading:
            current_lines.append(line)
    if current_heading and current_lines:
        fields.append((current_heading, "\n".join(current_lines).strip()))
    if not fields:
        st.markdown(f'<div class="summary-text">{escape(text)}</div>', unsafe_allow_html=True)
        return
    for heading, value in fields:
        st.markdown(f"**{heading.title()}**")
        st.markdown(f'<div class="summary-text">{escape(value)}</div>', unsafe_allow_html=True)


def split_cds_recommendations(recommendations):
    text = clean_clinical_text(recommendations)
    sections = {"Recommendation": "", "Reasoning": "", "Safety Note": ""}
    heading_pattern = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:\d+[.)]\s*)?(?:\*\*|__)?"
        r"(Clinical Context|Clinical Recommendation|Recommendation|"
        r"Reasoning|Evidence and Citations|Evidence|"
        r"Considerations for Clinician Review|Considerations|"
        r"Missing Information|Safety Note)"
        r"(?:\*\*|__)?\s*:?\s*(.*?)\s*$",
        re.IGNORECASE,
    )
    recommendation_parts = []
    current_section = None
    current_title = ""
    current_lines = []
    preamble = []

    def save_current():
        content = "\n".join(current_lines).strip()
        if not content:
            return
        if current_section == "Reasoning":
            sections["Reasoning"] = "\n\n".join(
                part for part in (sections["Reasoning"], content) if part
            )
        elif current_section == "Safety Note":
            sections["Safety Note"] = "\n\n".join(
                part for part in (sections["Safety Note"], content) if part
            )
        else:
            title = current_title if current_section != "Recommendation" else ""
            recommendation_parts.append(
                f"### {title}\n\n{content}" if title else content
            )

    for line in text.splitlines():
        match = heading_pattern.match(line)
        if match:
            save_current()
            title = match.group(1).strip()
            normalized_title = title.lower()
            if normalized_title in {"reasoning"}:
                current_section = "Reasoning"
            elif normalized_title == "safety note":
                current_section = "Safety Note"
            else:
                current_section = "Recommendation"
            current_title = title
            current_lines = [match.group(2)] if match.group(2) else []
        elif current_section:
            current_lines.append(line)
        else:
            preamble.append(line)
    save_current()

    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        recommendation_parts.insert(0, preamble_text)
    sections["Recommendation"] = "\n\n".join(recommendation_parts).strip()
    if not sections["Recommendation"] and not sections["Reasoning"] and not sections["Safety Note"]:
        sections["Recommendation"] = text
    return sections


def render_cds_recommendations(recommendations):
    sections = split_cds_recommendations(recommendations)
    tabs = st.tabs(list(sections.keys()))
    for tab, (label, content) in zip(tabs, sections.items()):
        with tab:
            if content:
                st.markdown(content)
            else:
                st.caption(f"No {label.lower()} provided.")


def render_code_results(values, empty_message):
    if not values:
        st.markdown(f'<div class="code-empty">{escape(empty_message)}</div>', unsafe_allow_html=True)
        return
    if isinstance(values, list) and values and isinstance(values[0], dict):
        columns = list(values[0].keys())
        header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(row.get(column, "")))}</td>" for column in columns) + "</tr>"
            for row in values
        )
        st.markdown(
            f'<div class="code-table-wrap"><table class="code-table"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>',
            unsafe_allow_html=True,
        )
    else:
        for value in values if isinstance(values, list) else [values]:
            st.markdown(f"- {escape(str(value))}")


def render_transcript(record, read_only=False):
    with st.expander("Transcript Audit Trail", expanded=False):
        doctor, patient = record.get("doctor", "Doctor"), record.get("name", "Patient")
        rows = record.get("transcript_data") or parse_transcript_entries(record.get("transcript", ""), doctor, patient)
        frame = pd.DataFrame(rows or [{"Speaker": doctor, "Text": "No transcript found."}])
        label = "Read-only clinical conversation" if read_only else "Editable clinical conversation"
        st.markdown(f'<div class="audit-note">{label} · {len(frame)} entries</div>', unsafe_allow_html=True)
        if read_only:
            st.dataframe(frame, use_container_width=True, hide_index=True)
            return frame
        return st.data_editor(frame, use_container_width=True, num_rows="dynamic", hide_index=True,
            column_config={"Speaker": st.column_config.SelectboxColumn("Speaker", options=[doctor, patient, "Unknown"], required=True), "Text": st.column_config.TextColumn("Spoken Text", width="large")})


def _chart_review_rows(sheet_name, entries):
    rows = []
    for item in entries:
        if sheet_name == "Problems":
            description = item.get("Problem_Name", "")
            code = item.get("ICD_10_CM", "")
            date = item.get("Onset_Date", "")
        elif sheet_name == "Allergies":
            description = " · ".join(
                str(value) for value in (
                    item.get("Substance", ""),
                    item.get("Reaction", ""),
                    item.get("Severity", ""),
                ) if value
            )
            code = item.get("Category", "")
            date = item.get("Recorded_Date", "")
        elif sheet_name == "Medications":
            description = " · ".join(
                str(value) for value in (
                    item.get("Medication", ""),
                    item.get("Dose", ""),
                    item.get("Route", ""),
                    item.get("Frequency", ""),
                ) if value
            )
            code = item.get("Indication_ICD_10", "")
            date = item.get("Start_Date", "")
        elif sheet_name == "Vitals":
            measurements = (
                ("BP", (
                    f'{item.get("Systolic_mmHg", "")}/'
                    f'{item.get("Diastolic_mmHg", "")} mmHg'
                ) if item.get("Systolic_mmHg") or item.get("Diastolic_mmHg") else ""),
                ("HR", f'{item.get("Heart_Rate_bpm")} bpm' if item.get("Heart_Rate_bpm") else ""),
                ("RR", f'{item.get("Respiratory_Rate")} /min' if item.get("Respiratory_Rate") else ""),
                ("SpO2", f'{item.get("SpO2_Percent")}%' if item.get("SpO2_Percent") else ""),
                ("Temp", f'{item.get("Temperature_F")} F' if item.get("Temperature_F") else ""),
                ("Weight", f'{item.get("Weight_kg")} kg' if item.get("Weight_kg") else ""),
                ("Height", f'{item.get("Height_cm")} cm' if item.get("Height_cm") else ""),
                ("BMI", item.get("BMI", "")),
            )
            description = " · ".join(
                f"{label}: {value}" for label, value in measurements if value
            )
            code = ""
            date = item.get("Observed_Date", "")
        elif sheet_name == "Labs":
            result = " ".join(
                str(value) for value in (item.get("Result", ""), item.get("Unit", ""))
                if value not in (None, "")
            )
            description = " · ".join(
                str(value) for value in (item.get("Test_Name", ""), result) if value
            )
            code = item.get("LOINC_Code", "")
            date = item.get("Result_Date", "")
        else:
            description = item.get("Order_Description", "")
            code = item.get("CPT_HCPCS", "")
            date = item.get("Order_Date", "")
        rows.append(
            {
                "Description": description,
                "Code": code,
                "Date": date,
                "Status": item.get("Clinical_Status") or item.get("Status", ""),
            }
        )
    return rows


# ============================================================
# 5. DASHBOARD
# ============================================================
def show_patient_data():
    render_sidebar("ehr_data")
    render_header("EHR Demo Data", "Synthetic U.S. patient records and hospital charges", show_new=False)
    client = HealthcareApiClient()
    try:
        payload = client.export_patient_data()
        patients = payload.get("sheets", {}).get("Patient_Master", {}).get("records", [])
    except (RuntimeError, requests.exceptions.RequestException, OSError, ValueError) as error:
        st.error(f"Unable to load Epic patient data from the backend: {error}")
        return

    sheets = payload.get("sheets", {})
    insurance_records = sheets.get("Insurance", {}).get("records", [])
    primary_coverage_by_patient = {}
    for coverage in insurance_records:
        if coverage.get("Status", "Active") != "Active":
            continue
        patient_key = str(coverage.get("Patient_ID", ""))
        existing_coverage = primary_coverage_by_patient.get(patient_key)
        if existing_coverage is None or coverage.get("Priority") == "Primary":
            primary_coverage_by_patient[patient_key] = coverage
    payer_by_patient = {
        patient_id: str(coverage.get("Payer_Name", ""))
        for patient_id, coverage in primary_coverage_by_patient.items()
    }
    st.caption(f"{len(patients)} synthetic patient chart(s) loaded from the backend. All active coverage uses Athena Health Insurance.")
    data_tab, bills_tab, chargemaster_tab = st.tabs(["Patient Data", "Bill History", "Athena Charges"])
    with data_tab:
        with st.expander("Add patient", expanded=False):
            with st.form("add_demo_patient_form"):
                first_col, last_col = st.columns(2)
                first_name = first_col.text_input("First name")
                last_name = last_col.text_input("Last name")
                new_patient_payer = "Athena Health Insurance"
                st.caption("Primary insurance payer: Athena Health Insurance")
                dob_col, sex_col = st.columns(2)
                dob = dob_col.date_input("Date of birth", value=datetime(1980, 1, 1).date())
                sex_at_birth = sex_col.selectbox("Sex at birth", ["Female", "Male", "Other"])
                city_col, state_col = st.columns(2)
                city = city_col.text_input("City")
                state = state_col.text_input("State")
                add_patient = st.form_submit_button("Add patient", type="primary")
            if add_patient:
                try:
                    created = client.create_patient(
                        {
                            "First_Name": first_name,
                            "Last_Name": last_name,
                            "DOB": dob.isoformat(),
                            "Sex_at_Birth": sex_at_birth,
                            "City": city,
                            "State": state,
                            "Payer_Name": new_patient_payer,
                        }
                    )
                    st.session_state["ehr_demo_patient"] = f'{created["Patient_ID"]} · {created["Legal_Name"]}'
                    st.success(f'Added {created["Legal_Name"]} ({created["Patient_ID"]}).')
                    st.rerun()
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"Patient could not be added: {error}")
        if not patients:
            st.info("No patient records are available.")
            return
        patient_options = {
            (
                f'{patient.get("Patient_ID", "")} · {patient.get("Legal_Name", "Unknown patient")} · '
                f'{payer_by_patient.get(str(patient.get("Patient_ID", ""))) or "Payer not assigned"}'
            ): patient
            for patient in patients
        }
        selected_label = st.selectbox("Patient", list(patient_options), key="ehr_demo_patient")
        selected_patient = patient_options[selected_label]
        patient_id = str(selected_patient.get("Patient_ID", ""))
        patient_payer = payer_by_patient.get(patient_id, "")
        if patient_payer:
            st.info(f"**Primary payer on file:** {patient_payer} · {primary_coverage_by_patient[patient_id].get('Plan_Type', '')}")
        else:
            st.warning("**Primary payer on file:** Not assigned. Choose a payer when generating a bill to add synthetic demo coverage.")
        try:
            chart = client.get_patient_chart(patient_id)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            st.error(f"Patient chart could not be loaded: {error}")
            return
        patient_sheets = [
            name for name in chart.get("records", {}) if name != "Audit_Log"
        ]
        encounter_count = len(chart.get("records", {}).get("Encounters", []))
        problem_count = len(chart.get("records", {}).get("Problems", []))
        medication_count = len(chart.get("records", {}).get("Medications", []))
        metric_columns = st.columns(5)
        metric_columns[0].metric("Patient ID", patient_id)
        metric_columns[1].metric("Encounters", encounter_count)
        metric_columns[2].metric("Problems", problem_count)
        metric_columns[3].metric("Medications", medication_count)
        metric_columns[4].metric("Primary Payer", patient_payer or "Not assigned")

        selected_sheet = st.selectbox(
            "Record type",
            patient_sheets,
            format_func=lambda name: name.replace("_", " "),
            key=f"ehr_data_sheet_{patient_id}",
        )
        patient_rows = chart.get("records", {}).get(selected_sheet, [])
        patient_headers = chart.get("headers", {}).get(selected_sheet, [])
        if selected_sheet == "Patient_Master":
            patient_rows = [
                {
                    **row,
                    "Payer_Name": payer_by_patient.get(str(row.get("Patient_ID", "")), ""),
                    "Plan_Type": primary_coverage_by_patient.get(
                        str(row.get("Patient_ID", "")), {}
                    ).get("Plan_Type", ""),
                }
                for row in patient_rows
            ]
            patient_headers = [*patient_headers, "Payer_Name", "Plan_Type"]
        frame = pd.DataFrame(patient_rows, columns=patient_headers)
        edited_frame = st.data_editor(
            frame,
            key=f"ehr_editor_{patient_id}_{selected_sheet}",
            use_container_width=True,
            hide_index=True,
            num_rows="fixed" if selected_sheet == "Patient_Master" else "dynamic",
            column_config={
                "Patient_ID": st.column_config.TextColumn("Patient ID", disabled=True),
                "Payer_Name": st.column_config.TextColumn(
                    "Primary Payer", disabled=True
                ),
                "Plan_Type": st.column_config.TextColumn("Plan", disabled=True),
            },
        )
        audit_rows = list(reversed(chart.get("records", {}).get("Audit_Log", [])))
        with st.expander(f"Audit evidence · {len(audit_rows)} EHR edit(s)"):
            st.caption(
                "Before/after snapshots are recorded by the backend when patient "
                "demographics or linked records are changed. This demo log is not "
                "tamper-proof and uses a demonstration actor label."
            )
            if audit_rows:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Time": row.get("Timestamp", ""),
                                "Actor": row.get("Actor", ""),
                                "Action": row.get("Action", ""),
                                "Record type": row.get("Record_Type", ""),
                                "Audit ID": row.get("Audit_ID", ""),
                            }
                            for row in audit_rows
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                selected_audit_index = st.selectbox(
                    "Inspect EHR before/after values",
                    range(len(audit_rows)),
                    format_func=lambda index: (
                        f'{audit_rows[index].get("Timestamp", "")} · '
                        f'{audit_rows[index].get("Action", "")} · '
                        f'{audit_rows[index].get("Record_Type", "")}'
                    ),
                    key=f"ehr_audit_{patient_id}",
                )
                st.json(
                    {
                        "before": audit_rows[selected_audit_index].get("Before", ""),
                        "after": audit_rows[selected_audit_index].get("After", ""),
                    }
                )
            else:
                st.info("No EHR edits are recorded for this patient yet.")
        save_column, download_column = st.columns([1, 1])
        if save_column.button("Save changes to JSON", type="primary", key=f"ehr_save_{patient_id}_{selected_sheet}"):
            edited_records = json.loads(edited_frame.to_json(orient="records", date_format="iso"))
            try:
                if selected_sheet == "Patient_Master":
                    editable_headers = set(chart.get("headers", {}).get("Patient_Master", []))
                    values = {
                        key: value
                        for key, value in edited_records[0].items()
                        if key in editable_headers and key != "Patient_ID"
                    }
                    client.update_patient(patient_id, values)
                else:
                    client.replace_patient_records(patient_id, selected_sheet, edited_records)
                st.success(f"Saved {selected_sheet.replace('_', ' ')} for {patient_id} through the backend.")
                st.rerun()
            except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                st.error(f"Changes could not be saved: {error}")
        download_column.download_button(
            "Download current JSON",
            data=json.dumps(client.export_patient_data(), indent=2, ensure_ascii=False),
            file_name="Epic_Inspired_USA_50_Patient_Demo_Athena.json",
            mime="application/json",
            key=f"ehr_download_{patient_id}_{selected_sheet}",
        )
        with st.expander("View edited rows as JSON"):
            st.json(json.loads(edited_frame.to_json(orient="records", date_format="iso")))

    with bills_tab:
        generated_bills = sheets.get("Generated_Bills", {}).get("records", [])
        historical_claims = sheets.get("Billing_Claims", {}).get("records", [])
        patient_names = {
            str(patient.get("Patient_ID", "")): str(patient.get("Legal_Name", "Unknown patient"))
            for patient in patients
        }
        generated_bill_ids = {str(item.get("Bill_ID", "")) for item in generated_bills}
        bill_history = []
        for bill in generated_bills:
            if not is_bill_history_visible(bill.get("Payer_Name")):
                continue
            bill_history.append(
                {
                    "Record": bill.get("Bill_ID", ""),
                    "Record Type": "Generated Bill",
                    "Patient": patient_names.get(str(bill.get("Patient_ID", "")), "Unknown patient"),
                    "Patient ID": bill.get("Patient_ID", ""),
                    "Encounter ID": bill.get("Encounter_ID", ""),
                    "Date": bill.get("Generated_At", ""),
                    "Description": f'{bill.get("Encounter_Type", "Facility")} facility bill',
                    "Gross Charge (USD)": bill.get("Gross_Total_USD", 0),
                    "Payer": bill.get("Payer_Name", ""),
                    "Expected Allowed (USD)": bill.get("Expected_Allowed_Total_USD", ""),
                    "Estimated Patient Share (USD)": bill.get("Patient_Responsibility_Total_USD", ""),
                    "Status": bill.get("Bill_Status") or "Draft",
                    "Bill ID": bill.get("Bill_ID", ""),
                }
            )
        for claim in historical_claims:
            if not is_bill_history_visible(claim.get("Payer")):
                continue
            claim_id = str(claim.get("Claim_ID", ""))
            if any(claim_id.startswith(f'CLM-{bill_id.removeprefix("BILL-")}-') for bill_id in generated_bill_ids):
                continue
            bill_history.append(
                {
                    "Record": claim_id,
                    "Record Type": "Historical Claim",
                    "Patient": patient_names.get(str(claim.get("Patient_ID", "")), "Unknown patient"),
                    "Patient ID": claim.get("Patient_ID", ""),
                    "Encounter ID": claim.get("Encounter_ID", ""),
                    "Date": claim.get("Service_Date", ""),
                    "Description": claim.get("Description", ""),
                    "Gross Charge (USD)": claim.get("Charge_USD", 0),
                    "Status": claim.get("Claim_Status", ""),
                    "Bill ID": "",
                }
            )

        if not bill_history:
            st.info("No historical claims or generated bills are available.")
        else:
            search_col, patient_col, status_col = st.columns([4, 2, 2])
            bill_search = search_col.text_input(
                "Search bills",
                placeholder="Patient, encounter, code, or service",
                key="nuucare_bill_history_search",
            ).strip().lower()
            patient_ids_for_bills = sorted(patient_names, key=lambda patient_id: patient_names[patient_id].lower())
            selected_bill_patient = patient_col.selectbox(
                "Patient filter",
                ["All patients", *patient_ids_for_bills],
                format_func=lambda patient_id: (
                    "All patients"
                    if patient_id == "All patients"
                    else f"{patient_names[patient_id]} · {patient_id}"
                ),
                key="bill_history_patient_filter",
            )
            statuses = sorted({str(item.get("Status", "")) for item in bill_history if item.get("Status")})
            selected_status = status_col.selectbox(
                "Status filter",
                ["All statuses", *statuses],
                key="nuucare_bill_history_status",
            )
            filtered_bills = [
                item for item in bill_history
                if (selected_bill_patient == "All patients" or item.get("Patient ID") == selected_bill_patient)
                and (selected_status == "All statuses" or item.get("Status") == selected_status)
                and (not bill_search or bill_search in " ".join(str(value) for value in item.values()).lower())
            ]
            visible_columns = [column for column in bill_history[0] if column != "Bill ID"]
            history_frame = pd.DataFrame(filtered_bills, columns=visible_columns)
            st.caption(f"Showing {len(filtered_bills)} historical bill or claim record(s) across all patients.")
            st.dataframe(history_frame, use_container_width=True, hide_index=True)
            st.download_button(
                "Download bill history CSV",
                data=history_frame.to_csv(index=False),
                file_name="nuucare_bill_history.csv",
                mime="text/csv",
                disabled=history_frame.empty,
            )
            bill_detail_options = {
                f'{item["Record Type"]} · {item["Record"]} · {item["Patient"]}': item
                for item in filtered_bills
            }
            if bill_detail_options:
                detail_label = st.selectbox("Bill detail", list(bill_detail_options), key="bill_history_detail")
                detail = bill_detail_options[detail_label]
                if detail.get("Bill ID"):
                    bill_lines = [
                        line for line in sheets.get("Generated_Bill_Lines", {}).get("records", [])
                        if line.get("Bill_ID") == detail["Bill ID"]
                    ]
                    if bill_lines:
                        st.dataframe(pd.DataFrame(bill_lines), use_container_width=True, hide_index=True)
                    else:
                        st.info("This bill has no saved charge-line detail.")
                else:
                    st.dataframe(pd.DataFrame([detail]), use_container_width=True, hide_index=True)

    with chargemaster_tab:
        source = PROJECT_ROOT.parent / "Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json"
        st.caption(f"Billing uses Athena Health Insurance rates from the IPD and OPD records in {source.name}.")
        try:
            with source.open("r", encoding="utf-8") as file:
                charge_data = json.load(file)
            charge_sheets = charge_data["sheets"]
            inpatient_charges = _athena_charge_preview(charge_sheets["IPD Charges"]["records"])
            outpatient_charges = _athena_charge_preview(charge_sheets["OPD Charges"]["records"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            st.error(f"Unable to load the Athena charge records: {error}")
        else:
            ipd_tab, opd_tab = st.tabs(["Inpatient (IPD)", "Outpatient (OPD)"])
            display_columns = [
                "CPT/HCPCS",
                "Charge Description",
                "Unit Charge ($)",
                "Expected Allowed ($)",
                "Patient Responsibility ($)",
                "Charge Status",
            ]
            with ipd_tab:
                st.dataframe(
                    inpatient_charges.loc[:, [column for column in display_columns if column in inpatient_charges]],
                    use_container_width=True,
                    hide_index=True,
                )
            with opd_tab:
                st.dataframe(
                    outpatient_charges.loc[:, [column for column in display_columns if column in outpatient_charges]],
                    use_container_width=True,
                    hide_index=True,
                )


def _athena_charge_preview(records):
    charges = pd.DataFrame(records, dtype=object)
    if "Payer" not in charges:
        raise ValueError("Athena charge records are missing the Payer column.")
    charges = charges.loc[
        charges["Payer"].eq("Athena Health Insurance")
    ].copy()
    for index, row in charges.iterrows():
        patient_share = row.get("Patient Responsibility ($)")
        if isinstance(patient_share, str) and patient_share.startswith("="):
            match = re.fullmatch(
                r"=P\d+\*([0-9]+(?:\.[0-9]+)?)%", patient_share
            )
            if not match:
                raise ValueError(f"Unsupported patient-share formula: {patient_share}")
            charges.at[index, "Patient Responsibility ($)"] = round(
                float(row["Expected Allowed ($)"]) * float(match.group(1)) / 100,
                2,
            )

        adjustment = row.get("Contractual Adjustment ($)")
        if isinstance(adjustment, str) and adjustment.startswith("="):
            if not re.fullmatch(r"=O\d+-P\d+", adjustment):
                raise ValueError(f"Unsupported contractual-adjustment formula: {adjustment}")
            charges.at[index, "Contractual Adjustment ($)"] = round(
                float(row["Unit Charge ($)"]) * float(row["Quantity"])
                - float(row["Expected Allowed ($)"]),
                2,
            )
    return charges


def show_dashboard():
    render_sidebar("dashboard")
    render_header("Clinical Operations", "Consultation and documentation overview")
    st.markdown('<div class="toolbar-label">RECORD DIRECTORY</div>', unsafe_allow_html=True)
    filter_search, filter_date, filter_status = st.columns([5, 2, 3])
    search_query = filter_search.text_input("Search patient or ID", placeholder="Search patient or ID").strip().lower()
    date_filter = filter_date.selectbox("Date", ["All Time", "Today", "Last 7 Days", "Last 30 Days", "Custom Date Range"])
    status_filter = filter_status.selectbox("Status", ["All Statuses", "Pending", "Approved"])
    custom_dates = st.date_input("Custom Range", value=(datetime.now().date(), datetime.now().date())) if date_filter == "Custom Date Range" else None
    today, filtered = datetime.now().date(), []
    for record in st.session_state.records:
        searchable = f'{record.get("name", "")} {record.get("id", "")}'.lower()
        if search_query and search_query not in searchable:
            continue
        if status_filter != "All Statuses" and record.get("status") != status_filter:
            continue
        try:
            record_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date()
        except (ValueError, TypeError):
            continue
        keep = date_filter == "All Time"
        if date_filter == "Today": keep = record_date == today
        elif date_filter == "Last 7 Days": keep = 0 <= (today - record_date).days <= 7
        elif date_filter == "Last 30 Days": keep = 0 <= (today - record_date).days <= 30
        elif date_filter == "Custom Date Range" and isinstance(custom_dates, tuple): keep = len(custom_dates) == 2 and custom_dates[0] <= record_date <= custom_dates[1]
        if keep: filtered.append(record)
    total = len(filtered)
    pending = sum(r.get("status") == "Pending" for r in filtered)
    approved = sum(r.get("status") == "Approved" for r in filtered)
    today_count = sum(r.get("date") == today.strftime("%d/%m/%Y") for r in filtered)
    kpis = st.columns(4)
    for col, item in zip(kpis, [("Total Consultations", total, "#0f766e", "All recorded consults"), ("Today's Volume", today_count, "#2563eb", "Created today"), ("Pending Review", pending, "#b45309", "Requires attention"), ("Approved Notes", approved, "#15803d", "Completed documentation")]):
        with col: render_kpi_card(*item)
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
    chart_col, overview_col = st.columns([6.5, 3.5], gap="large")
    with chart_col:
        st.markdown('<div class="section-title">Consultation Activity</div><div class="section-subtitle">Daily consultation throughput and backlog</div>', unsafe_allow_html=True)
        events = []
        for record in filtered:
            events.append({"Date": record.get("date"), "Metric": "New Consults"})
            if record.get("status") == "Approved": events.append({"Date": record.get("approval_date", record.get("date")), "Metric": "Approved"})
        if events:
            frame = pd.DataFrame(events)
            frame["Date"] = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
            counts = frame.dropna(subset=["Date"]).groupby(["Date", "Metric"]).size().reset_index(name="Count")
            chart = alt.Chart(counts).mark_line(point=True).encode(
                x=alt.X("Date:T", title="Date", axis=alt.Axis(format="%b %d, %Y")),
                y=alt.Y("Count:Q", title="Records"),
                color=alt.Color("Metric:N", legend=alt.Legend(title="")),
                tooltip=[alt.Tooltip("Date:T", title="Date", format="%b %d, %Y"), "Metric:N", "Count:Q"],
            ).properties(height=220).configure(
                background="#ffffff" if theme_name == "Light" else "#1e293b"
            ).configure_view(
                strokeWidth=0
            ).configure_axis(
                labelColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                titleColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                gridColor="#e2e8f0" if theme_name == "Light" else "#334155",
            ).configure_legend(
                labelColor="#17212b" if theme_name == "Light" else "#e5e7eb",
                titleColor="#17212b" if theme_name == "Light" else "#e5e7eb",
            )
            st.altair_chart(chart, use_container_width=True)
        else: st.info("No consultation activity is available for the selected filters.")
    with overview_col:
        approval_rate = round(approved / total * 100) if total else 0
        st.markdown('<div class="section-title">Clinical Overview</div><div class="section-subtitle">Current operating position</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="overview-grid"><div><small>Pending review</small><b class="amber-text">{pending}</b></div><div><small>Approval rate</small><b class="green-text">{approval_rate}%</b></div><div><small>Today\'s consultations</small><b class="blue-text">{today_count}</b></div></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-gap"></div><div class="section-title">Patient Records</div><div class="section-subtitle">Clinical documentation queue</div>', unsafe_allow_html=True)
    if filtered:
        render_patient_table(filtered)
    else:
        st.info("No clinical records found.")


# ============================================================
# 6. NEW CONSULTATION
# ============================================================
def has_first_and_last_name(name):
    return len(str(name or "").split()) >= 2


def register_consultation_in_ehr(record, client=None, patient=None, dob=None):
    client = client or HealthcareApiClient()
    patient = patient or {}
    patient_id = str(record.get("patient_id", "") or patient.get("Patient_ID", ""))
    if not patient_id:
        patient_name = str(record.get("name", "")).strip()
        if not has_first_and_last_name(patient_name):
            raise ValueError("Enter the patient's first and last name before saving to Epic.")
        first_name, last_name = patient_name.split(maxsplit=1)
        if not dob:
            try:
                approximate_age = max(0, int(record.get("age", "")))
            except (TypeError, ValueError) as error:
                raise ValueError("A date of birth is required to add this patient to Epic.") from error
            dob = f"{max(1900, datetime.now().year - approximate_age)}-01-01"
        patient = client.create_patient(
            {
                "First_Name": first_name,
                "Last_Name": last_name,
                "DOB": dob,
                "Sex_at_Birth": record.get("gender", "Other"),
            }
        )
        patient_id = str(patient["Patient_ID"])

    record["patient_id"] = patient_id
    record["mrn"] = patient.get("MRN", record.get("mrn", ""))
    record["dob"] = patient.get("DOB", dob or record.get("dob", ""))
    patient_age = patient.get("Age")
    if isinstance(patient_age, (int, float)) or str(patient_age).isdigit():
        record["age"] = str(patient_age)
    try:
        service_date = datetime.strptime(record.get("date", ""), "%d/%m/%Y").date().isoformat()
    except ValueError:
        service_date = datetime.now().date().isoformat()
    if not record.get("ehr_encounter_id"):
        encounter = client.create_patient_record(
            patient_id,
            "Encounters",
            {
                "Date": service_date,
                "Type": "Office Visit",
                "Department": "Primary Care",
                "Provider": record.get("doctor", "Unassigned"),
                "Facility": "Northstar Medical Center",
                "Status": "Completed",
                "Primary_Diagnosis": consultation_assessment(record),
                "Priority": "Routine",
            },
        )
        record["ehr_encounter_id"] = encounter["Encounter_ID"]
    if not record.get("ehr_note_id"):
        note = client.create_patient_record(
            patient_id,
            "Clinical_Notes",
            {
                "Encounter_ID": record["ehr_encounter_id"],
                "Note_Type": "SOAP",
                "Service_Date": service_date,
                "Author": record.get("doctor", "Unassigned"),
                "Note_Summary": record.get("summary") or record.get("patient_summary", ""),
                "Status": "Draft" if record.get("status") == "Pending" else "Signed",
            },
        )
        record["ehr_note_id"] = note["Note_ID"]
    return record


def update_ehr_clinical_note(record, approved=False):
    patient_id = record.get("patient_id")
    note_id = record.get("ehr_note_id")
    if not patient_id or not note_id:
        return
    client = HealthcareApiClient()
    chart = client.get_patient_chart(patient_id)
    notes = chart.get("records", {}).get("Clinical_Notes", [])
    for note in notes:
        if note.get("Note_ID") == note_id:
            note["Note_Summary"] = record.get("summary", "")
            note["Status"] = "Signed" if approved else "Draft"
            break
    client.replace_patient_records(patient_id, "Clinical_Notes", notes)


def show_new_consultation():
    render_sidebar("new_consult")
    render_header("New Consultation", "Capture patient context and generate clinical documentation", show_new=False)
    st.markdown('<div class="workflow-steps"><b>01 Patient</b><span>02 Record</span><span>03 Generate</span><span>04 Review</span></div>', unsafe_allow_html=True)
    if st.button("Back to Dashboard"):
        go_to("dashboard"); st.rerun()
    with st.container(border=True):
        st.markdown('<div class="section-title">Patient Information</div><div class="section-subtitle">Enter the basic context for this consultation</div>', unsafe_allow_html=True)
        try:
            demo_patients = HealthcareApiClient().list_patients()
        except (RuntimeError, requests.exceptions.RequestException) as error:
            demo_patients = []
            st.warning(
                "The EHR patient list is unavailable. You can continue with manual "
                f"entry, but the consultation cannot link to an existing demo patient: {error}"
            )
        demo_patient_options = {
            f'{patient.get("Patient_ID", "")} · {patient.get("Legal_Name", "Unknown patient")}': patient
            for patient in demo_patients
        }
        patient_source = st.selectbox(
            "Patient source",
            ["Manual entry", *demo_patient_options],
            key="consult_patient_source",
        )
        selected_demo_patient = demo_patient_options.get(patient_source)
        patient_widget_key = str(selected_demo_patient.get("Patient_ID")) if selected_demo_patient else "manual"
        default_name = str(selected_demo_patient.get("Legal_Name", "")) if selected_demo_patient else ""
        default_age = ""
        default_dob = ""
        if selected_demo_patient:
            try:
                default_dob = str(selected_demo_patient.get("DOB", "")).split("T")[0]
                birth_date = datetime.fromisoformat(default_dob).date()
                today = datetime.now().date()
                default_age = str(today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day)))
            except ValueError:
                pass
        gender_options = ["Male", "Female", "Other"]
        default_gender = str(selected_demo_patient.get("Sex_at_Birth", "Other")) if selected_demo_patient else "Male"
        gender_index = gender_options.index(default_gender) if default_gender in gender_options else 2
        if selected_demo_patient:
            st.caption("Using a synthetic U.S. demo patient. Patient ID and MRN will be linked to the consultation.")
        left, right = st.columns(2, gap="large")
        patient_name = left.text_input("Full Name", value=default_name, key=f"consult_patient_name_{patient_widget_key}", placeholder="e.g. John Doe")
        name_is_valid = bool(selected_demo_patient) or has_first_and_last_name(patient_name)
        if not selected_demo_patient and patient_name.strip() and not name_is_valid:
            st.info("Enter both the patient's first and last name, or select an existing EHR patient.")
        patient_age = right.text_input("Age", value=default_age, key=f"consult_patient_age_{patient_widget_key}")
        patient_gender = left.selectbox("Gender", gender_options, index=gender_index, key=f"consult_patient_gender_{patient_widget_key}")
        patient_dob = left.text_input("Date of birth (YYYY-MM-DD)", value=default_dob, key=f"consult_patient_dob_{patient_widget_key}", disabled=bool(selected_demo_patient), placeholder="Required for a new Epic patient")
        doctor_name = right.text_input("Attending Doctor", placeholder="e.g. Dr. Sarah Jenkins")
    with st.container(border=True):
        st.markdown('<div class="section-title">Record Consultation</div><div class="section-subtitle">Create a synthetic demo encounter; audio is processed by an external speech-recognition service.</div>', unsafe_allow_html=True)
        st.warning(
            "Demo only — do not record or enter real protected health information. "
            "Audio is sent to Google Speech Recognition; the transcript is sent "
            "to the configured clinical backend and model."
        )
        audio_file = st.audio_input("Record Audio", label_visibility="collapsed")
        st.markdown('<div class="recording-prompt">Audio input ready when you are</div>', unsafe_allow_html=True)
        if st.button(
            "Transcribe and Generate Summary",
            type="primary",
            use_container_width=True,
            disabled=audio_file is None or not patient_name.strip() or not name_is_valid,
        ):
            try:
                with st.spinner("Transcribing audio..."):
                    recognizer = sr.Recognizer()
                    with sr.AudioFile(io.BytesIO(audio_file.getvalue())) as source:
                        transcript = recognizer.recognize_google(
                            recognizer.record(source)
                        )
            except (sr.UnknownValueError, sr.RequestError, OSError, ValueError) as error:
                st.error(f"Audio could not be transcribed: {error}")
            else:
                with st.spinner("Generating clinical summary and CDS recommendations..."):
                    try:
                        st.session_state.scribe_result = (
                            HealthcareApiClient().analyze_encounter(transcript)
                        )
                    except (RuntimeError, requests.exceptions.RequestException) as error:
                        st.session_state.scribe_result = {
                            "transcript": transcript,
                            "soap_note": "",
                            "patient_summary": "",
                            "key_highlights": [],
                            "retrieved_guidelines": "",
                            "recommendations": "",
                        }
                        st.warning(
                            "The clinical summary service is unavailable, so the "
                            "transcript was kept. Local ICD-10/CPT matching can still "
                            f"continue. Details: {error}"
                        )
                    else:
                        st.rerun()
        result = st.session_state.scribe_result
        if result:
            generation_metadata = result.get("metadata", {})
            if generation_metadata.get("soap_generation_status") == "transcript_fallback":
                st.warning(
                    "A structured SOAP response could not be obtained after retrying. "
                    "A transcript-only note was prepared without adding clinical facts. "
                    "Review it before saving."
                )
            st.markdown('<div class="section-title">Transcript</div>', unsafe_allow_html=True)
            st.text_area("Generated transcript", value=result.get("transcript", ""), height=150, disabled=True, label_visibility="collapsed")
            st.markdown('<div class="section-title">Summary and Key Highlights</div>', unsafe_allow_html=True)
            render_summary(result.get("soap_note") or result.get("patient_summary", "Not available"))
            for highlight in result.get("key_highlights", []): st.markdown(f"- {highlight}")
            if generation_metadata.get("cds_generation_status") == "unavailable":
                st.warning(
                    "The SOAP note is ready, but CDS could not be generated. "
                    "The transcript is preserved; retry CDS from the review screen."
                )
            elif result.get("recommendations"):
                st.success("Clinical Decision Support recommendations generated.")
                with st.container(border=True):
                    st.markdown('<div class="section-title">Clinical Decision Support Recommendations</div><div class="section-subtitle">Evidence-based guidance for this encounter</div>', unsafe_allow_html=True)
                    render_cds_recommendations(result["recommendations"])
            else:
                st.info("Clinical Decision Support recommendations will be generated when you save this note.")
            if st.button(
                "Generate Note",
                type="primary",
                use_container_width=True,
                disabled=not name_is_valid,
            ):
                encounter = result
                try:
                    if not encounter.get("recommendations"):
                        encounter = HealthcareApiClient().add_cds(encounter)
                except (RuntimeError, requests.exceptions.HTTPError) as error:
                    generation_metadata = dict(encounter.get("metadata", {}))
                    generation_metadata["cds_generation_status"] = "unavailable"
                    generation_metadata["cds_generation_error"] = str(error)
                    st.warning(
                        "Clinical decision support is unavailable, so the encounter will be saved "
                        f"without recommendations. Local ICD-10/CPT matching can still continue. Details: {error}"
                    )
                else:
                    generation_metadata = dict(encounter.get("metadata", {}))
                    if encounter.get("recommendations"):
                        generation_metadata["cds_generation_status"] = "generated"
                        generation_metadata.pop("cds_generation_error", None)
                transcript = encounter.get("transcript", "")
                doctor, now = doctor_name.strip() or "Doctor", datetime.now()
                record = {"id": f"PT-{str(uuid.uuid4().int)[:6]}", "name": patient_name.strip(), "age": patient_age.strip(), "gender": patient_gender, "doctor": doctor, "date": now.strftime("%d/%m/%Y"), "time": now.strftime("%H:%M"), "status": "Pending", "transcript": transcript, "transcript_data": parse_transcript_entries(transcript, doctor, patient_name.strip()), "summary": encounter.get("soap_note", ""), "patient_summary": encounter.get("patient_summary", ""), "key_highlights": encounter.get("key_highlights", []), "retrieved_guidelines": encounter.get("retrieved_guidelines", ""), "recommendations": encounter.get("recommendations", ""), "generation_metadata": generation_metadata, "cds_requested": True, "analysis_version": ANALYSIS_VERSION, "dob": patient_dob.strip()}
                try:
                    register_consultation_in_ehr(record, patient=selected_demo_patient, dob=patient_dob.strip() or None)
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"The consultation could not be registered in Epic: {error}")
                    return
                st.session_state.records.insert(0, record)
                save_db(st.session_state.records)
                open_record(record)
                st.rerun()


# ============================================================
# 7. REVIEW
# ============================================================
def show_review_note():
    render_sidebar("review")
    record = st.session_state.current_record
    if not record:
        go_to("dashboard"); st.rerun(); return
    is_approved = record.get("status") == "Approved"
    back, title, status = st.columns([1.15, 6.85, 2], vertical_alignment="center")
    if back.button("Back", key="review_back", use_container_width=True):
        go_to("dashboard"); st.rerun()
    title.markdown(f'<div class="page-heading">{escape(record.get("name", ""))}</div><div class="page-kicker">{escape(record.get("id", ""))} · {escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))} · {escape(record.get("doctor", "N/A"))}</div>', unsafe_allow_html=True)
    status.markdown(render_status_badge(record.get("status", "Pending")), unsafe_allow_html=True)
    generation_metadata = record.get("generation_metadata") or {}
    if generation_metadata.get("soap_generation_status") == "transcript_fallback":
        st.warning(
            "This SOAP note contains the source transcript in a structured shell "
            "because automated SOAP extraction was unavailable. Verify it before approval."
        )
    if st.session_state.last_record_id != record.get("id"):
        st.session_state.edit_summary = record.get("summary") or record.get("patient_summary", "")
        st.session_state.last_record_id = record.get("id")
    left, right = st.columns([6.2, 3.8], gap="large")
    with left:
        with st.container(border=True):
            st.markdown('<div class="section-title">Transcript</div><div class="section-subtitle">Source conversation used for analysis</div>', unsafe_allow_html=True)
            with st.expander("View source conversation", expanded=True):
                st.text_area("Transcript", value=record.get("transcript", "No transcript available."), height=150, disabled=True, label_visibility="collapsed")

            soap_col, cds_col = st.columns(2)
            with soap_col:
                if not is_approved and st.button("Generate SOAP", type="primary", use_container_width=True, disabled=not record.get("transcript", "").strip()):
                    try:
                        with st.spinner("Generating SOAP note..."):
                            regenerated = HealthcareApiClient().regenerate_encounter(record["transcript"])
                        previous_soap = record.get("summary", "")
                        record["summary"] = regenerated.get("soap_note") or regenerated.get("patient_summary", "")
                        record["patient_summary"] = regenerated.get("patient_summary", "")
                        record["key_highlights"] = regenerated.get("key_highlights", [])
                        record["transcript"] = regenerated.get("transcript", record.get("transcript", ""))
                        record["generation_metadata"] = regenerated.get("metadata", {})
                        append_audit_event(
                            record,
                            "SOAP note regenerated",
                            {"summary": previous_soap},
                            {"summary": record["summary"]},
                        )
                        update_ehr_clinical_note(record)
                        for index, existing in enumerate(st.session_state.records):
                            if existing.get("id") == record.get("id"):
                                st.session_state.records[index] = record
                                break
                        save_db(st.session_state.records)
                        st.session_state.edit_summary = record["summary"]
                        st.success("SOAP note generated from the stored transcript.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.HTTPError) as error:
                        st.error(f"SOAP regeneration failed: {error}")
            with cds_col:
                if not is_approved and st.button("Regenerate CDS from Transcript", type="secondary", use_container_width=True, disabled=not record.get("transcript", "").strip()):
                    try:
                        with st.spinner("Regenerating CDS from the consultation transcript..."):
                            regenerated = HealthcareApiClient().regenerate_cds(record)
                        recommendations = regenerated.get("recommendations")
                        if not recommendations or not str(recommendations).strip():
                            raise RuntimeError("The CDS service returned no recommendation. The existing recommendation was kept.")
                        previous_recommendation = record.get("recommendations", "")
                        record["retrieved_guidelines"] = regenerated.get("retrieved_guidelines", record.get("retrieved_guidelines", ""))
                        record["recommendations"] = recommendations
                        generation_metadata = {
                            **(record.get("generation_metadata") or {}),
                            **regenerated.get("metadata", {}),
                            "cds_generation_status": "generated",
                        }
                        generation_metadata.pop("cds_generation_error", None)
                        record["generation_metadata"] = generation_metadata
                        record["cds_requested"] = True
                        record["analysis_version"] = ANALYSIS_VERSION
                        append_audit_event(
                            record,
                            "CDS recommendation regenerated",
                            {"recommendations": previous_recommendation},
                            {"recommendations": recommendations},
                        )
                        for index, existing in enumerate(st.session_state.records):
                            if existing.get("id") == record.get("id"):
                                st.session_state.records[index] = record
                                break
                        save_db(st.session_state.records)
                        st.success("CDS recommendations regenerated from the consultation transcript.")
                        st.rerun()
                    except (RuntimeError, requests.exceptions.HTTPError) as error:
                        st.error(f"CDS regeneration failed: {error}")
        with st.container(border=True):
            st.markdown('<div class="section-title">Clinical Decision Support Recommendations</div><div class="section-subtitle">Evidence-based guidance for this encounter</div>', unsafe_allow_html=True)
            recommendations = str(record.get("recommendations") or "").strip()
            if recommendations:
                render_cds_recommendations(recommendations)
                recommendation_review = record.get("recommendation_review", {})
                if recommendation_review:
                    st.info(
                        f"Clinician review: {recommendation_review.get('decision', '').title()} · "
                        f"{recommendation_review.get('reviewer', 'Reviewer')} · "
                        f"{recommendation_review.get('reviewed_at', '')}"
                    )
                    if recommendation_review.get("rationale"):
                        st.caption(f"Review rationale: {recommendation_review['rationale']}")
                if not is_approved:
                    decision_col, rationale_col, save_decision_col = st.columns([1.3, 3, 1.2])
                    review_decision = decision_col.selectbox(
                        "Recommendation decision",
                        ["Pending", "Accepted", "Overridden"],
                        index=(
                            ["pending", "accepted", "overridden"].index(
                                str(recommendation_review.get("decision", "pending")).lower()
                            )
                            if str(recommendation_review.get("decision", "pending")).lower()
                            in {"pending", "accepted", "overridden"}
                            else 0
                        ),
                        key=f"cds_decision_{record.get('id')}",
                    )
                    review_rationale = rationale_col.text_input(
                        "Override rationale",
                        value=str(recommendation_review.get("rationale", "")),
                        key=f"cds_rationale_{record.get('id')}",
                        disabled=review_decision != "Overridden",
                    )
                    if save_decision_col.button(
                        "Save review",
                        key=f"save_cds_review_{record.get('id')}",
                        disabled=review_decision == "Pending",
                    ):
                        try:
                            update_recommendation_review(
                                record,
                                review_decision,
                                review_rationale,
                            )
                            for index, existing in enumerate(st.session_state.records):
                                if existing.get("id") == record.get("id"):
                                    st.session_state.records[index] = record
                                    break
                            save_db(st.session_state.records)
                            st.success("Recommendation review decision saved with before/after audit evidence.")
                            st.rerun()
                        except ValueError as error:
                            st.error(str(error))
            else:
                st.warning(
                    "No CDS recommendations were returned for this encounter. "
                    "Regenerate CDS from the transcript; do not treat this notice "
                    "as clinical guidance."
                )
        with st.container(border=True):
            st.markdown('<div class="section-title">Relationship Graph</div><div class="section-subtitle">Clinical relationships detected in this encounter</div>', unsafe_allow_html=True)
            render_clinical_graph(record.get("name", "Patient"), st.session_state.detected_insights)
    with right:
        with st.container(border=True):
            subtitle = "Approved and locked" if is_approved else "Editable encounter summary"
            st.markdown(f'<div class="section-title">SOAP Note</div><div class="section-subtitle">{subtitle}</div>', unsafe_allow_html=True)
            render_summary(st.session_state.edit_summary)
            if not is_approved:
                with st.expander("Review and edit summary", expanded=False): st.text_area("Edit clinical summary", key="edit_summary", height=220, label_visibility="collapsed")
        with st.container(border=True):
            st.markdown('<div class="section-title">Detected Insights</div><div class="section-subtitle">Grouped clinical signals</div>', unsafe_allow_html=True)
            insights = st.session_state.detected_insights
            if insights:
                render_grouped_insights(insights)
            else:
                st.caption("No insights detected in this transcript.")
            st.caption("ICD-10 and CPT/HCPCS suggestions are available on the Codes page.")
            if st.button("Open Codes", key="review_open_codes", use_container_width=True):
                go_to("codes"); st.rerun()
        if not is_approved:
            with st.container(border=True):
                st.markdown('<div class="insight-heading">Add Clinical Insight</div>', unsafe_allow_html=True)
                for index in range(st.session_state.insight_count): st.selectbox(f"Insight {index + 1}", MASTER_INSIGHTS, key=f"insight_{index}", label_visibility="collapsed")
                add_col, push_col = st.columns(2)
                if add_col.button("Add Insight", use_container_width=True, disabled=st.session_state.insight_count >= 6):
                    st.session_state.insight_count += 1; st.rerun()
                def push_selected_insights_to_note():
                    previous_summary = record.get("summary", "")
                    previous_insights = list(record.get("manual_insights", []))
                    selected = [st.session_state.get(f"insight_{i}", "") for i in range(st.session_state.insight_count)]
                    manual_insights = record.setdefault("manual_insights", [])
                    for insight in selected:
                        if insight != "None" and insight not in manual_insights:
                            manual_insights.append(insight)
                    st.session_state.edit_summary = append_selected_insights_to_note(st.session_state.edit_summary, selected)
                    record["summary"] = st.session_state.edit_summary
                    st.session_state.detected_insights = get_record_insights(record)
                    append_audit_event(
                        record,
                        "Clinical insight added to note",
                        {
                            "summary": previous_summary,
                            "manual_insights": previous_insights,
                        },
                        {
                            "summary": record["summary"],
                            "manual_insights": list(record.get("manual_insights", [])),
                        },
                    )
                    update_ehr_clinical_note(record)
                    for index, existing in enumerate(st.session_state.records):
                        if existing.get("id") == record.get("id"):
                            st.session_state.records[index] = record
                            break
                    save_db(st.session_state.records)
                    st.session_state.insight_count = 1
                push_col.button("Push to Note", type="primary", use_container_width=True, on_click=push_selected_insights_to_note)
    edited_df = render_transcript(record, read_only=is_approved)
    def persist(approved=False):
        if record.get("status") == "Approved":
            return False
        previous = {
            "summary": record.get("summary", ""),
            "transcript_data": record.get("transcript_data", []),
            "status": record.get("status", "Pending"),
        }
        record["summary"] = st.session_state.edit_summary
        record["transcript_data"] = edited_df.to_dict("records")
        try:
            update_ehr_clinical_note(record, approved=approved)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            record["summary"] = previous["summary"]
            record["transcript_data"] = previous["transcript_data"]
            st.error(f"The note could not be synchronized to Epic: {error}")
            return False
        if approved:
            record["status"] = "Approved"
            record["approval_date"] = datetime.now().strftime("%d/%m/%Y")
        append_audit_event(
            record,
            "Clinical note finalized" if approved else "Clinical note draft saved",
            previous,
            {
                "summary": record.get("summary", ""),
                "transcript_data": record.get("transcript_data", []),
                "status": record.get("status", "Pending"),
            },
        )
        for index, existing in enumerate(st.session_state.records):
            if existing.get("id") == record.get("id"):
                st.session_state.records[index] = record; break
        save_db(st.session_state.records)
        return True

    audit_events = list(reversed(record.get("audit_log", [])))
    with st.expander(f"Audit evidence and before/after history · {len(audit_events)} event(s)"):
        st.caption(
            "Local demonstration audit history. It records changes to the consultation "
            "workspace and is not a tamper-proof or authenticated clinical audit system."
        )
        if audit_events:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Timestamp": event.get("timestamp", ""),
                            "Actor": event.get("actor", ""),
                            "Action": event.get("action", ""),
                            "Rationale": event.get("rationale", ""),
                        }
                        for event in audit_events
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            selected_event = st.selectbox(
                "Inspect before/after change",
                range(len(audit_events)),
                format_func=lambda index: (
                    f"{audit_events[index].get('timestamp', '')} · "
                    f"{audit_events[index].get('action', '')}"
                ),
                key=f"audit_event_{record.get('id')}",
            )
            st.json(
                {
                    "before": audit_events[selected_event].get("before", {}),
                    "after": audit_events[selected_event].get("after", {}),
                    "rationale": audit_events[selected_event].get("rationale", ""),
                }
            )
        else:
            st.info("No review edits have been recorded yet.")
    if record.get("status") == "Pending":
        st.divider()
        _, draft_col, final_col = st.columns([7, 1.5, 1.5], vertical_alignment="center")
        if draft_col.button("Save Draft", use_container_width=True):
            if persist(False): st.success("Draft and audit trail updated.")
        if final_col.button("Finalize Note", type="primary", use_container_width=True):
            if persist(True): go_to("dashboard"); st.rerun()


# ============================================================
# 8. CODES PAGE
# ============================================================
def show_codes():
    render_sidebar("codes")
    render_header("Codes", "ICD-10 diagnosis and CPT/HCPCS procedure code suggestions")

    records = st.session_state.records
    if not records:
        st.info("No patient encounters are available. Create a consultation before generating codes.")
        return

    record_lookup = {f'{r.get("name", "Unknown")} · {r.get("id", "No ID")}': r for r in records}
    current = st.session_state.current_record
    default_label = next((label for label, value in record_lookup.items() if current and value.get("id") == current.get("id")), list(record_lookup)[0])
    labels = list(record_lookup)
    selected_label = st.selectbox("Select patient encounter", labels, index=labels.index(default_label))
    record = record_lookup[selected_label]
    if st.session_state.generated_bill_record_id != record.get("id"):
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None
        st.session_state.generated_bill_signature = None
        st.session_state.bill_validation_signature = None
        st.session_state.bill_validation_errors = None

    if not current or current.get("id") != record.get("id"):
        st.session_state.current_record = record
        st.session_state.detected_insights = get_record_insights(record)

    insights = get_record_insights(record)
    st.session_state.detected_insights = insights

    st.markdown(
        f'<div class="code-hero"><div class="code-hero-title">Medical Coding Workspace</div>'
        f'<div class="code-hero-text">Review machine-suggested codes before adding them to the clinical or billing workflow.</div>'
        f'<div class="code-patient"><b>{escape(record.get("name", ""))}</b> · {escape(record.get("id", ""))} · '
        f'{escape(str(record.get("age", "")))}y · {escape(record.get("gender", ""))} · {escape(record.get("doctor", "N/A"))}</div></div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown('<div class="section-title">Detected Clinical Context</div><div class="section-subtitle">Items used to identify candidate codes</div>', unsafe_allow_html=True)
        if insights:
            render_grouped_insights(insights)
        else:
            st.warning("No codable insights were detected in this encounter transcript.")

        generate_col, review_col = st.columns([1, 1])
        if generate_col.button("Regenerate Code Suggestions", type="primary", use_container_width=True, disabled=not insights):
            with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
                st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
                signature = code_results_signature(record, insights)
                st.session_state.code_record_id = record.get("id")
                st.session_state.code_results_signature = signature
                st.session_state.code_matcher_version = CODE_MATCHER_VERSION
            st.rerun()
        if review_col.button("Return to Clinical Review", use_container_width=True):
            go_to("review"); st.rerun()

    current_signature = code_results_signature(record, insights)
    cached_results_are_stale = (
        st.session_state.code_record_id != record.get("id")
        or st.session_state.code_results_signature != current_signature
        or st.session_state.code_matcher_version != CODE_MATCHER_VERSION
    )
    if cached_results_are_stale:
        with st.spinner("Matching ICD-10 and CPT/HCPCS codes..."):
            st.session_state.code_results = get_backend_code_suggestions(insights, record.get("transcript", ""))
        st.session_state.code_record_id = record.get("id")
        st.session_state.code_results_signature = current_signature
        st.session_state.code_matcher_version = CODE_MATCHER_VERSION

    code_results = st.session_state.code_results
    code_error = st.session_state.get("code_generation_error", "")
    if code_error:
        st.warning(code_error)
    elif st.session_state.code_record_id == record.get("id"):
        total_matches = len(code_results.get("icd10", [])) + len(code_results.get("cpt", []))
        st.success(f"Code matching completed. {total_matches} suggestion(s) found.")

    unified_rows = [
        {
            "Documentation / service": item.get("Extracted Condition", ""),
            "Code type": "ICD-10-CM",
            "Code": item.get("ICD-10 Code", ""),
            "Purpose": item.get("Matched Disease/Injury", ""),
        }
        for item in code_results.get("icd10", [])
    ] + [
        {
            "Documentation / service": item.get("Extracted Procedure", ""),
            "Code type": "CPT/HCPCS",
            "Code": item.get("CPT/HCPCS Code", ""),
            "Purpose": item.get("Matched Procedure/Service", ""),
        }
        for item in code_results.get("cpt", [])
    ]
    with st.container(border=True):
        st.markdown('<div class="section-title">Unified Diagnosis and Procedure Codes</div><div class="section-subtitle">Review this encounter\'s codes before generating its patient-specific gross-charge bill</div>', unsafe_allow_html=True)
        if unified_rows:
            st.dataframe(pd.DataFrame(unified_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No diagnosis or procedure codes are available for this encounter.")
    with st.expander("Coding evidence and provenance"):
        st.caption(
            f"Matcher version {CODE_MATCHER_VERSION}. Suggestions below were matched "
            "from this saved transcript and detected clinical context; they require "
            "professional review."
        )
        st.markdown("**Source transcript**")
        st.text(record.get("transcript") or "No transcript is saved.")
        st.markdown("**Detected context**")
        st.write(insights or ["No clinical context was detected."])
        if code_results.get("icd10") or code_results.get("cpt"):
            st.markdown("**Candidate mapping evidence**")
            st.json(
                {
                    "ICD-10-CM": code_results.get("icd10", []),
                    "CPT/HCPCS": code_results.get("cpt", []),
                }
            )

    try:
        demo_patients = HealthcareApiClient().list_patients()
    except (RuntimeError, requests.exceptions.RequestException) as error:
        demo_patients = []
        st.error(f"EHR patients could not be loaded from the backend: {error}")
    all_patient_lookup = {
        str(patient.get("Patient_ID", "")): patient for patient in demo_patients
    }
    patient_lookup = {
        str(patient.get("Patient_ID", "")): patient
        for patient in billing_eligible_patients(demo_patients)
    }
    patient_ids = list(patient_lookup)
    stored_patient_id = str(record.get("patient_id", "")).strip()
    linked_patient_id = linked_patient_id_for_record(record, patient_lookup)
    matching_patient_ids = matching_patient_ids_for_record(record, patient_lookup)
    default_patient_id = linked_patient_id or (
        matching_patient_ids[0] if len(matching_patient_ids) == 1 else ""
    )
    selected_patient_id = ""
    selected_bill_encounter = ""
    encounter_id = None
    selected_payer = ""
    current_bill_signature = None
    selected_patient_chart = {"records": {}}
    if patient_ids:
        if (
            not linked_patient_id
            and stored_patient_id in all_patient_lookup
            and is_seed_demo_patient(stored_patient_id)
        ):
            st.info(
                "The original 50 demo patients are excluded from bill creation. "
                "Select John F. Kennedy or a patient added after the demo set."
            )
        elif not linked_patient_id:
            if matching_patient_ids:
                st.warning(
                    "The saved patient link is missing or mismatched. Select the matching EHR patient to repair the link."
                )
            else:
                st.warning(
                    f'{record.get("name", "This consultation")} is not in the current EHR dataset. '
                    "Create a matching EHR patient before billing."
                )
                if st.button(
                    f'Create EHR patient for {record.get("name", "this consultation")}',
                    key=f"create_ehr_patient_{record.get('id', 'encounter')}",
                ):
                    record_to_register = dict(record)
                    record_to_register["patient_id"] = ""
                    record_to_register.pop("ehr_encounter_id", None)
                    record_to_register.pop("ehr_note_id", None)
                    record_to_register.pop("mrn", None)
                    try:
                        register_consultation_in_ehr(
                            record_to_register,
                            dob=record.get("dob"),
                        )
                        for index, existing in enumerate(st.session_state.records):
                            if existing.get("id") == record.get("id"):
                                st.session_state.records[index] = record_to_register
                                break
                        st.session_state.current_record = record_to_register
                        save_db(st.session_state.records)
                        st.rerun()
                    except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                        st.error(f"The consultation could not be linked to a new EHR patient: {error}")

        has_unambiguous_match = bool(default_patient_id)
        selected_patient_id = st.selectbox(
            "Bill for EHR patient",
            patient_ids if has_unambiguous_match else ["", *patient_ids],
            index=patient_ids.index(default_patient_id) if has_unambiguous_match else 0,
            format_func=lambda patient_id: (
                "Select an EHR patient"
                if not patient_id
                else f'{patient_id} · {patient_lookup[patient_id].get("Legal_Name", "")} · '
                f'{patient_lookup[patient_id].get("Payer_Name") or "Payer not assigned"}'
            ),
            key=f"bill_patient_v2_{record.get('id', 'encounter')}_{default_patient_id or 'unlinked'}",
        )
        if not selected_patient_id:
            st.selectbox(
                "Insurance payer for this bill",
                SUPPORTED_PAYER_OPTIONS,
                index=0,
                key=f"bill_payer_unlinked_{record.get('id', 'encounter')}",
                help="Choose the payer rate sheet to use after the consultation is linked to its EHR patient.",
            )
            st.button(
                "Generate Bill",
                type="primary",
                disabled=True,
                key=f"generate_bill_unlinked_{record.get('id', 'encounter')}",
            )
            st.button(
                "Validate Bill",
                disabled=True,
                key=f"validate_bill_unlinked_{record.get('id', 'encounter')}",
                help="Generate and link a bill before validating it.",
            )
            st.info("Create the matching EHR patient or select and register an existing patient before billing.")
            return
        try:
            selected_patient_chart = HealthcareApiClient().get_patient_chart(selected_patient_id)
        except (RuntimeError, requests.exceptions.RequestException) as error:
            selected_patient_chart = {"records": {}}
            st.error(f"Encounter list could not be loaded: {error}")
        patient_encounters = selected_patient_chart.get("records", {}).get("Encounters", [])
        patient_notes = selected_patient_chart.get("records", {}).get("Clinical_Notes", [])
        if (
            not linked_patient_id
            and patient_name_matches_record(record, patient_lookup[selected_patient_id])
        ):
            st.caption("Repair this consultation's EHR link to the matching patient before billing.")
            if st.button(
                "Link consultation to matching EHR patient",
                key=f"link_consult_{selected_patient_id}_{record.get('id', 'encounter')}",
            ):
                record_to_register = dict(record)
                record_to_register["patient_id"] = selected_patient_id
                if not any(
                    item.get("Encounter_ID") == record_to_register.get("ehr_encounter_id")
                    for item in patient_encounters
                ):
                    record_to_register.pop("ehr_encounter_id", None)
                    record_to_register.pop("ehr_note_id", None)
                elif not any(
                    item.get("Note_ID") == record_to_register.get("ehr_note_id")
                    for item in patient_notes
                ):
                    record_to_register.pop("ehr_note_id", None)
                try:
                    register_consultation_in_ehr(
                        record_to_register,
                        patient=patient_lookup[selected_patient_id],
                    )
                    for index, existing in enumerate(st.session_state.records):
                        if existing.get("id") == record.get("id"):
                            st.session_state.records[index] = record_to_register
                            break
                    st.session_state.current_record = record_to_register
                    save_db(st.session_state.records)
                    st.rerun()
                except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                    st.error(f"The consultation could not be linked to the matching EHR patient: {error}")
        encounter_lookup = {
            str(item.get("Encounter_ID", "")): item
            for item in patient_encounters
            if item.get("Encounter_ID")
        }
        encounter_options = ["Create new encounter"] + [
            encounter_id for encounter_id in encounter_lookup
        ]
        preferred_encounter_id = str(record.get("ehr_encounter_id", ""))
        if preferred_encounter_id not in encounter_lookup:
            preferred_encounter_id = next(
                (
                    encounter_id for encounter_id, item in encounter_lookup.items()
                    if "billing" not in str(item.get("Type", "")).lower()
                ),
                "",
            )
        default_encounter = preferred_encounter_id or encounter_options[0]
        selected_bill_encounter = st.selectbox(
            "Patient encounter",
            encounter_options,
            index=encounter_options.index(default_encounter),
            format_func=lambda value: (
                "Create new encounter from these codes"
                if value == encounter_options[0]
                else f'{value} · {encounter_lookup[value].get("Type", "Encounter")} · {encounter_lookup[value].get("Date", "")}'
            ),
            key=f"bill_encounter_v2_{selected_patient_id}_{record.get('id', 'encounter')}",
        )
        encounter_id = None if selected_bill_encounter == encounter_options[0] else selected_bill_encounter
        if encounter_id:
            selected_encounter_type = encounter_lookup[encounter_id].get("Type", "")
            bill_encounter_type = "Inpatient" if "inpatient" in str(selected_encounter_type).lower() else "Outpatient"
        else:
            bill_encounter_type = st.selectbox(
                "New encounter type",
                ["Outpatient", "Inpatient"],
                key=f"new_bill_encounter_type_{selected_patient_id}_{record.get('id', 'encounter')}",
            )
        chart_records = selected_patient_chart.get("records", {})
        selected_encounter = encounter_lookup.get(encounter_id or "", {})
        encounter_notes = [
            note for note in chart_records.get("Clinical_Notes", [])
            if note.get("Encounter_ID") == encounter_id
            and note.get("Note_Summary")
        ]
        with st.expander("Patient chart review", expanded=True):
            st.markdown(
                f"**{patient_lookup[selected_patient_id].get('Legal_Name', selected_patient_id)}** "
                f"({selected_patient_id}) · Athena Health Insurance"
            )
            if encounter_id:
                st.markdown(
                    f"**Selected encounter:** {encounter_id} · "
                    f"{selected_encounter.get('Type', 'Encounter')} · "
                    f"{selected_encounter.get('Date', 'Date not recorded')} · "
                    f"{selected_encounter.get('Status', 'Status not recorded')}"
                )
                st.markdown(
                    "**Encounter assessment:** "
                    + encounter_diagnosis_label(selected_encounter, encounter_notes)
                )
            if encounter_notes:
                st.markdown("**Clinical documentation**")
                for note in encounter_notes:
                    note_meta = " · ".join(
                        str(value)
                        for value in (
                            note.get("Note_Type"),
                            note.get("Service_Date"),
                            note.get("Status"),
                        )
                        if value
                    )
                    if note_meta:
                        st.caption(note_meta)
                    sections = soap_sections(note.get("Note_Summary", ""))
                    if sections:
                        for section in ("Subjective", "Objective", "Assessment", "Plan"):
                            content = sections.get(section)
                            if content:
                                st.markdown(f"**{section}**")
                                st.write(content)
                    else:
                        st.text(note.get("Note_Summary", ""))
            elif encounter_id:
                st.caption("No clinical note is linked to this encounter.")
            chart_groups = [
                ("Problems", "Problems"),
                ("Allergies", "Allergies"),
                ("Medications", "Medications"),
                ("Vitals", "Vitals"),
                ("Labs", "Labs"),
                ("Orders and procedures", "Orders_Procedures"),
            ]
            displayed_groups = 0
            for label, sheet_name in chart_groups:
                entries = chart_records.get(sheet_name, [])
                if encounter_id and sheet_name not in {"Problems", "Allergies", "Medications"}:
                    entries = [
                        item for item in entries
                        if item.get("Encounter_ID") in {"", None, encounter_id}
                    ]
                if entries:
                    displayed_groups += 1
                    st.markdown(f"**{label} · {len(entries)}**")
                    st.dataframe(
                        pd.DataFrame(_chart_review_rows(sheet_name, entries)),
                        use_container_width=True,
                        hide_index=True,
                    )
            if not displayed_groups:
                st.caption(
                    "Encounter details are available in the linked clinical note above."
                )
        selected_payer = "Athena Health Insurance"
        st.caption("Insurance payer for this bill: Athena Health Insurance")
        note_encounter_id = str(record.get("ehr_encounter_id", ""))
        use_note_codes = (
            bool(linked_patient_id) and selected_patient_id == linked_patient_id
            and (encounter_id is None or not note_encounter_id or encounter_id == note_encounter_id)
        )
        suggested_cpt_codes = [
            str(item.get("CPT/HCPCS Code", ""))
            for item in code_results.get("cpt", [])
            if item.get("CPT/HCPCS Code")
        ]
        if linked_patient_id and not use_note_codes:
            use_note_codes = st.checkbox(
                f"Apply this consultation's CPT codes to {patient_lookup[selected_patient_id].get('Legal_Name', selected_patient_id)}",
                help="Use only when these reviewed codes belong to the selected patient's encounter.",
                key=f"apply_consult_codes_{selected_patient_id}_{record.get('id', 'encounter')}",
            )
        encounter_order_codes = [
            str(item.get("CPT_HCPCS", "")).strip()
            for item in selected_patient_chart.get("records", {}).get("Orders_Procedures", [])
            if item.get("Encounter_ID") == encounter_id and item.get("CPT_HCPCS")
        ]
        may_use_encounter_codes = bool(linked_patient_id) and selected_patient_id == linked_patient_id
        codes_for_bill = (
            suggested_cpt_codes
            if use_note_codes
            else encounter_order_codes if may_use_encounter_codes else []
        )
        query_encounter_id = encounter_id or str(record.get("ehr_encounter_id", ""))
        query_code_options = ["General documentation clarification"] + [
            f'{item.get("Code type", "")} {item.get("Code", "")} · '
            f'{item.get("Purpose", "")}'
            for item in unified_rows
            if item.get("Code")
        ]
        with st.expander("Physician documentation query", expanded=False):
            st.caption(
                "Draft a neutral clarification for the provider. This tool does not "
                "infer a diagnosis, alter a code, or send the query externally."
            )
            if not query_encounter_id:
                st.info("Link this consultation to an EHR encounter before creating a query.")
            else:
                with st.form(f"physician_query_form_{record.get('id')}_{query_encounter_id}"):
                    query_topic = st.selectbox(
                        "Query topic",
                        query_code_options,
                        key=f"physician_query_topic_{record.get('id')}",
                    )
                    query_question = st.text_area(
                        "Neutral clarification question",
                        placeholder="Enter a non-leading question for the treating provider.",
                        key=f"physician_query_question_{record.get('id')}",
                    )
                    query_indicators = st.text_area(
                        "Documented clinical indicators",
                        value=str(record.get("summary") or record.get("transcript") or ""),
                        key=f"physician_query_indicators_{record.get('id')}",
                        help="Use documented facts only. Verify the selected text before saving.",
                    )
                    query_options = st.text_input(
                        "Provider response options (optional)",
                        placeholder="For example: clinically supported / not supported / unable to determine",
                        key=f"physician_query_options_{record.get('id')}",
                    )
                    save_query = st.form_submit_button("Save query draft")
                if save_query:
                    try:
                        create_physician_query(
                            record,
                            selected_patient_id,
                            query_encounter_id,
                            query_topic,
                            query_question,
                            query_indicators,
                            query_options,
                        )
                        save_db(st.session_state.records)
                        st.success("Physician query draft saved to the consultation record.")
                        st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                saved_queries = [
                    query for query in record.get("physician_queries", [])
                    if query.get("patient_id") == selected_patient_id
                    and query.get("encounter_id") == query_encounter_id
                ]
                if saved_queries:
                    st.markdown("**Saved queries for this encounter**")
                    for query in saved_queries:
                        with st.container(border=True):
                            st.markdown(
                                f'**{query.get("status", "Draft")} · '
                                f'{query.get("topic", "Documentation clarification")}**'
                            )
                            st.write(query.get("question", ""))
                            st.caption(
                                f'Clinical indicators: {query.get("clinical_indicators", "")}'
                            )
                            if query.get("response_options"):
                                st.caption(f'Response options: {query["response_options"]}')
                            if query.get("provider_response"):
                                st.success(f'Provider response: {query["provider_response"]}')
                            else:
                                with st.form(
                                    f"physician_query_response_{query.get('query_id')}"
                                ):
                                    response = st.text_area(
                                        "Record provider response",
                                        key=f"query_response_{query.get('query_id')}",
                                    )
                                    save_response = st.form_submit_button(
                                        "Save provider response"
                                    )
                                if save_response:
                                    try:
                                        record_physician_query_response(
                                            record,
                                            query["query_id"],
                                            response,
                                        )
                                        save_db(st.session_state.records)
                                        st.success("Provider response recorded.")
                                        st.rerun()
                                    except (KeyError, ValueError) as error:
                                        st.error(str(error))
        services_confirmed_performed = False
        if codes_for_bill:
            attestation_encounter = encounter_id or f"new-{bill_encounter_type.lower()}"
            attestation_codes = "-".join(codes_for_bill)
            services_confirmed_performed = st.checkbox(
                "I reviewed these CPT/HCPCS codes and confirm the services were performed and documented for this encounter.",
                key=(
                    f"bill_services_confirmed_{selected_patient_id}_"
                    f"{record.get('id', 'encounter')}_{attestation_encounter}_"
                    f"{attestation_codes}"
                ),
            )
        icd10_codes_for_bill = (
            [
                str(item.get("ICD-10 Code", ""))
                for item in code_results.get("icd10", [])
                if item.get("ICD-10 Code")
            ]
            if use_note_codes
            else []
        )
        if not codes_for_bill:
            if not linked_patient_id:
                st.warning("Link this consultation to its EHR patient before generating a bill.")
            else:
                st.warning(
                    "This encounter has no billable codes linked to the selected patient. "
                    "Select the checkbox only if the displayed consultation codes belong to this patient."
                )
        current_bill_signature = (
            str(record.get("id", "")),
            selected_patient_id,
            encounter_id or "",
            bill_encounter_type,
            selected_payer,
            code_results_signature(record, insights),
            tuple(icd10_codes_for_bill),
            tuple(codes_for_bill),
            services_confirmed_performed,
        )
        existing_bill = find_matching_bill(
            selected_patient_chart.get("records", {}).get("Generated_Bills", []),
            selected_patient_id,
            encounter_id,
            selected_payer,
            codes_for_bill,
        )
        already_generated_for_context = bool(
            (
                st.session_state.generated_bill
                and st.session_state.generated_bill_record_id == record.get("id")
                and st.session_state.generated_bill_patient_id == selected_patient_id
                and st.session_state.generated_bill_signature == current_bill_signature
            )
            or existing_bill
        )
        if already_generated_for_context:
            if existing_bill:
                with st.container(border=True):
                    st.markdown("**Bill already generated**")
                    bill_id_col, bill_status_col, bill_total_col = st.columns(3)
                    bill_id_col.metric("Bill ID", existing_bill.get("Bill_ID", ""))
                    bill_status_col.metric(
                        "Status", existing_bill.get("Bill_Status", "Draft")
                    )
                    bill_total_col.metric(
                        "Gross total",
                        f'${float(existing_bill.get("Gross_Total_USD", 0) or 0):,.2f}',
                    )
                    saved_lines = [
                        line for line in chart_records.get("Generated_Bill_Lines", [])
                        if line.get("Bill_ID") == existing_bill.get("Bill_ID")
                    ]
                    if saved_lines:
                        st.dataframe(
                            pd.DataFrame(saved_lines),
                            use_container_width=True,
                            hide_index=True,
                        )
                    st.caption("This saved bill is also listed under EHR Demo Data → Bill History.")
        if st.button(
            "Generate Bill",
            type="primary",
            disabled=not can_generate_bill(
                codes_for_bill,
                services_confirmed_performed,
                selected_payer in SUPPORTED_PAYER_OPTIONS,
                already_generated_for_context,
            ),
            help=(
                "This bill is already generated for the current encounter and code set."
                if already_generated_for_context
                else "Billing stays disabled until you confirm that the selected services were performed and documented."
            ),
        ):
            try:
                bill = HealthcareApiClient().generate_patient_bill(
                    selected_patient_id,
                    icd10_codes_for_bill,
                    codes_for_bill,
                    encounter_id=encounter_id,
                    encounter_type=bill_encounter_type,
                    payer_name=selected_payer,
                )
                st.session_state.generated_bill = bill
                st.session_state.generated_bill_record_id = record.get("id")
                st.session_state.generated_bill_patient_id = selected_patient_id
                st.session_state.generated_bill_signature = current_bill_signature
                st.session_state.bill_validation_signature = None
                st.session_state.bill_validation_errors = None
                st.rerun()
            except (RuntimeError, requests.exceptions.RequestException, ValueError) as error:
                st.error(f"Bill generation failed: {error}")
                warning = revenue_leakage_warning(error)
                if warning:
                    st.warning(warning)
    else:
        st.warning("Add an EHR patient before generating and saving a bill.")

    bill = st.session_state.generated_bill
    if (
        bill
        and (
            st.session_state.generated_bill_record_id != record.get("id")
            or st.session_state.generated_bill_patient_id != selected_patient_id
            or st.session_state.generated_bill_signature != current_bill_signature
            or bill.get("Patient_ID") != selected_patient_id
            or (encounter_id and bill.get("Encounter_ID") != encounter_id)
        )
    ):
        st.session_state.generated_bill = None
        st.session_state.generated_bill_record_id = None
        st.session_state.generated_bill_patient_id = None
        st.session_state.generated_bill_signature = None
        st.session_state.bill_validation_signature = None
        st.session_state.bill_validation_errors = None
        bill = None
    validation_col, validation_message_col = st.columns([1, 5], vertical_alignment="center")
    if validation_col.button(
        "Validate Bill",
        disabled=not bool(
            bill
            and not bill.get("Unpriced_Codes")
            and selected_patient_id
            and current_bill_signature
        ),
        key=f"validate_bill_{record.get('id', 'encounter')}_{selected_patient_id}",
        help="Checks bill patient, encounter, payer, priced lines, and gross total. It does not submit a claim.",
    ):
        st.session_state.bill_validation_errors = validate_generated_bill(
            bill,
            selected_patient_id,
            encounter_id,
            selected_payer,
        )
        st.session_state.bill_validation_signature = current_bill_signature
        st.rerun()
    if st.session_state.bill_validation_signature == current_bill_signature:
        validation_errors = st.session_state.bill_validation_errors or []
        if validation_errors:
            validation_message_col.error("Bill validation failed: " + " ".join(validation_errors))
        elif bill:
            validation_message_col.success(
                f"Bill validated for {bill.get('Patient_Name') or selected_patient_id} "
                f"with payer {bill.get('Payer_Name') or 'Not selected'}. "
                "No claim was submitted."
            )
    if bill and bill.get("Patient_ID") == selected_patient_id:
        st.markdown('<div class="section-gap"></div><div class="section-title">Generated Hospital Bill</div><div class="section-subtitle">Gross charges and Athena estimated allowed amounts</div>', unsafe_allow_html=True)
        if bill.get("Unpriced_Codes"):
            st.error(
                "This saved bill is incomplete and cannot be validated. The "
                "following codes were omitted because no Athena rate exists: "
                + ", ".join(str(code) for code in bill["Unpriced_Codes"])
                + ". Generate a new bill after verified rates are added."
            )
        gross_total = float(bill.get("Gross_Total_USD", 0) or 0)
        allowed_total = float(bill.get("Expected_Allowed_Total_USD", 0) or 0)
        patient_total = float(bill.get("Patient_Responsibility_Total_USD", 0) or 0)
        st.info(f"**Insurance payer for this bill:** {bill.get('Payer_Name') or 'Not selected'}")
        total_col, allowed_col, patient_share_col, account_col = st.columns(4)
        total_col.metric("Gross Charges", f"${gross_total:,.2f}")
        allowed_col.metric(
            "Expected Allowed",
            f"${allowed_total:,.2f}" if bill.get("Payer_Name") else "Not calculated",
        )
        patient_share_col.metric(
            "Estimated Patient Share",
            f"${patient_total:,.2f}" if bill.get("Payer_Name") else "Not calculated",
        )
        account_col.metric("Hospital Account", bill.get("Hospital_Account", "N/A"))
        st.dataframe(pd.DataFrame(bill.get("Line_Items", [])), use_container_width=True, hide_index=True)
        st.caption(f'{bill.get("Notice", "Gross charges only.")} {bill.get("Bill_Status", "Draft")} · Saved to the selected EHR demo JSON as {bill.get("Bill_ID", "")} for {bill.get("Patient_ID", "")} / {bill.get("Encounter_ID", "")} .')
# ============================================================
# 9. ROUTER
# ============================================================
if st.session_state.page == "dashboard":
    show_dashboard()
elif st.session_state.page == "new_consult":
    show_new_consultation()
elif st.session_state.page == "ehr_data":
    show_patient_data()
elif st.session_state.page == "review":
    show_review_note()
elif st.session_state.page == "codes":
    show_codes()
else:
    st.session_state.page = "dashboard"
    st.rerun()
