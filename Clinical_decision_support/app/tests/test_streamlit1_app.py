import pytest

from frontend.streamlit1_app import (
    _athena_charge_preview,
    _chart_review_rows,
    register_consultation_in_ehr,
    code_results_signature,
    get_code_suggestions,
    get_record_insights,
    get_detected_insights,
    is_seed_demo_patient,
    linked_patient_id_for_record,
    matching_patient_ids_for_record,
    is_bill_history_visible,
    split_cds_recommendations,
    validate_generated_bill,
)
from frontend.clinical_workflow import (
    append_audit_event,
    billing_eligible_patients,
    can_generate_bill,
    create_physician_query,
    consultation_assessment,
    encounter_diagnosis_label,
    find_matching_bill,
    matching_bill_exists,
    record_physician_query_response,
    revenue_leakage_warning,
    soap_sections,
    update_recommendation_review,
)


def test_code_results_signature_changes_when_documentation_changes():
    insights = ["CT Scan", "Established Patient Office Visit"]
    first_record = {"id": "visit-1", "transcript": "CT scan ordered."}
    second_record = {"id": "visit-1", "transcript": "CT scan performed."}

    assert code_results_signature(first_record, insights) != code_results_signature(
        second_record, insights
    )


def test_orphaned_patient_link_is_not_used_for_billing():
    patient_lookup = {
        "P100001": {"Patient_ID": "P100001", "Legal_Name": "Olivia Johnson"},
        "P100051": {"Patient_ID": "P100051", "Legal_Name": "Donald Trump"},
    }

    assert linked_patient_id_for_record(
        {"patient_id": "P100052"}, patient_lookup
    ) == ""
    assert linked_patient_id_for_record(
        {"patient_id": "P100001", "name": "Olivia Johnson"}, patient_lookup
    ) == "P100001"
    assert linked_patient_id_for_record(
        {"patient_id": "P100051", "name": "john f kennedy"}, patient_lookup
    ) == ""
    assert matching_patient_ids_for_record(
        {"name": "Donald Trump"}, patient_lookup
    ) == ["P100051"]
    assert matching_patient_ids_for_record(
        {"name": "john f kennedy"}, patient_lookup
    ) == []


def test_seed_patient_range_identifies_fixed_payer_demo_patients():
    assert is_seed_demo_patient("P100001")
    assert is_seed_demo_patient("P100050")
    assert not is_seed_demo_patient("P100051")
    assert not is_seed_demo_patient("invalid")


def test_bill_patient_options_exclude_only_the_original_50_demo_patients():
    patients = [
        {"Patient_ID": "P100001"},
        {"Patient_ID": "P100050"},
        {"Patient_ID": "P100051", "Legal_Name": "John F Kennedy"},
        {"Patient_ID": "P100052", "Legal_Name": "Donald Trump"},
        {"Patient_ID": "P100053", "Legal_Name": "New Patient"},
    ]

    assert [item["Patient_ID"] for item in billing_eligible_patients(patients)] == [
        "P100051",
        "P100052",
        "P100053",
    ]


def test_bill_generation_requires_attestation_and_blocks_duplicates():
    assert not can_generate_bill(["99213"], False, True, False)
    assert not can_generate_bill(["99213"], True, True, True)
    assert not can_generate_bill(["99213"], True, False, False)
    assert can_generate_bill(["99213"], True, True, False)


def test_encounter_diagnosis_label_does_not_show_transcript_as_diagnosis():
    assert encounter_diagnosis_label(
        {"Primary_Diagnosis": "History of Present Illness: " + ("clinical narrative " * 40)}
    ) == "Not recorded"
    assert encounter_diagnosis_label(
        {
            "Primary_Diagnosis": "Subjective: patient reports cough.",
            "Primary_Diagnosis_Code": "J18.9",
        }
    ) == "J18.9"
    assert encounter_diagnosis_label(
        {"Primary_Diagnosis": "Community-acquired pneumonia"}
    ) == "Community-acquired pneumonia"


def test_soap_note_assessment_is_shown_in_encounter_chart():
    note = (
        "Subjective:\nReports cough.\n\n"
        "Objective:\nTemperature 101.8 F.\n\n"
        "Assessment:\nSuspected pneumonia with asthma exacerbation.\n\n"
        "Plan:\nOrder chest X-ray."
    )

    assert consultation_assessment({"summary": note}) == (
        "Suspected pneumonia with asthma exacerbation."
    )
    assert soap_sections(note)["Objective"] == "Temperature 101.8 F."
    assert encounter_diagnosis_label(
        {
            "Encounter_ID": "ENC1",
            "Primary_Diagnosis": "History of Present Illness: long transcript",
        },
        [{"Encounter_ID": "ENC1", "Note_Summary": note}],
    ) == "Suspected pneumonia with asthma exacerbation."
    assert encounter_diagnosis_label(
        {"Encounter_ID": "ENC2"},
        [{"Encounter_ID": "ENC1", "Note_Summary": note}],
    ) == "Not recorded"


def test_new_encounter_uses_soap_assessment_not_transcript_as_primary_diagnosis():
    class FakeClient:
        def __init__(self):
            self.created = []

        def create_patient_record(self, patient_id, sheet_name, values):
            self.created.append((patient_id, sheet_name, values))
            return {
                "Encounter_ID": "ENC1",
                "Note_ID": "NOTE1",
            }

    client = FakeClient()
    record = {
        "patient_id": "P100053",
        "name": "Dwayne Singh",
        "date": "08/10/2026",
        "patient_summary": "History of Present Illness: transcript narrative",
        "summary": (
            "Subjective:\nCough.\n\nAssessment:\nAsthma exacerbation.\n\n"
            "Plan:\nFollow up."
        ),
    }

    register_consultation_in_ehr(
        record,
        client=client,
        patient={"Patient_ID": "P100053", "Age": 40, "MRN": "MRN1"},
    )

    encounter = next(values for _, sheet, values in client.created if sheet == "Encounters")
    assert encounter["Primary_Diagnosis"] == "Asthma exacerbation."
    assert "History of Present Illness" not in encounter["Primary_Diagnosis"]


def test_matching_bill_exists_for_same_patient_encounter_payer_and_code_set():
    bill = {
            "Patient_ID": "P100053",
            "Encounter_ID": "ENC00506",
            "Payer_Name": "Athena Health Insurance",
            "CPT_HCPCS_Codes": ["85025", "82947", "71045", "99213"],
            "Bill_Status": "Draft",
        }

    assert matching_bill_exists(
        [bill],
        "P100053",
        "ENC00506",
        "Athena Health Insurance",
        ["99213", "71045", "82947", "85025"],
    )
    assert find_matching_bill(
        [bill],
        "P100053",
        "ENC00506",
        "Athena Health Insurance",
        ["99213", "71045", "82947", "85025"],
    ) == bill
    newest = {**bill, "Bill_ID": "BILL-NEWEST"}
    assert find_matching_bill(
        [bill, newest],
        "P100053",
        "ENC00506",
        "Athena Health Insurance",
        ["99213", "71045", "82947", "85025"],
    ) == newest
    assert not matching_bill_exists(
        [bill], "P100053", "ENC00507", "Athena Health Insurance",
        ["85025", "82947", "71045", "99213"],
    )
    assert not matching_bill_exists(
        [{**bill, "Unpriced_Codes": ["71045"]}],
        "P100053",
        "ENC00506",
        "Athena Health Insurance",
        ["85025", "82947", "71045", "99213"],
    )


def test_recommendation_acceptance_and_override_are_audited():
    record = {"id": "visit-1", "recommendations": "Review the documented symptoms."}

    with pytest.raises(ValueError, match="rationale is required"):
        update_recommendation_review(record, "Overridden")

    accepted = update_recommendation_review(record, "Accepted")
    assert accepted["decision"] == "accepted"
    assert record["audit_log"][-1]["action"] == "CDS recommendation accepted"
    assert record["audit_log"][-1]["before"] == {}
    assert record["audit_log"][-1]["after"]["decision"] == "accepted"

    overridden = update_recommendation_review(
        record, "Overridden", "The recommendation does not reflect the encounter."
    )
    assert overridden["decision"] == "overridden"
    assert record["audit_log"][-1]["rationale"] == (
        "The recommendation does not reflect the encounter."
    )


def test_physician_query_draft_and_response_are_saved_with_audit_events():
    record = {"id": "visit-1", "physician_queries": []}

    with pytest.raises(ValueError, match="clinical indicators"):
        create_physician_query(
            record,
            "P100053",
            "ENC00506",
            "CPT/HCPCS 71045",
            "Please clarify the documented imaging service.",
            "",
        )

    query = create_physician_query(
        record,
        "P100053",
        "ENC00506",
        "CPT/HCPCS 71045",
        "Please clarify the documented imaging service.",
        "The note documents a one-view chest radiograph.",
        "One view / other / unable to determine",
    )
    assert query["status"] == "Draft"
    assert query["patient_id"] == "P100053"
    assert record["audit_log"][-1]["action"] == "Physician query drafted"

    answered = record_physician_query_response(
        record,
        query["query_id"],
        "One-view imaging was performed.",
    )
    assert answered["status"] == "Answered"
    assert record["audit_log"][-1]["action"] == (
        "Physician query response recorded"
    )


def test_append_audit_event_keeps_independent_before_after_snapshots():
    before = {"status": "Pending"}
    record = {"audit_log": []}
    event = append_audit_event(
        record, "Status changed", before, {"status": "Approved"}
    )
    before["status"] = "Changed outside event"

    assert event["before"] == {"status": "Pending"}
    assert record["audit_log"] == [event]


def test_bill_validation_checks_payer_patient_and_line_totals():
    bill = {
        "Patient_ID": "P100051",
        "Encounter_ID": "ENC00504",
        "Payer_Name": "Athena Health Insurance",
        "Gross_Total_USD": 410,
        "Line_Items": [
            {"Gross Charge (USD)": 250, "Payer": "Athena Health Insurance"},
            {"Gross Charge (USD)": 160, "Payer": "Athena Health Insurance"},
        ],
        "Unpriced_Codes": [],
    }

    assert validate_generated_bill(
        bill, "P100051", "ENC00504", "Athena Health Insurance"
    ) == []
    assert validate_generated_bill(
        bill, "P100051", "ENC00504", "Care Gap Health"
    ) == [
        "The bill payer does not match the selected payer.",
        "A bill line does not use the selected payer.",
    ]
    invalid_total = {**bill, "Gross_Total_USD": 411}
    assert validate_generated_bill(
        invalid_total, "P100051", "ENC00504", "Athena Health Insurance"
    ) == ["The line-item gross charges do not equal the bill total."]


def test_missing_price_surfaces_revenue_leakage_warning_only_for_pricing_failures():
    warning = revenue_leakage_warning(
        "Bill was not generated because CPT/HCPCS code 93000 has no Athena rate."
    )

    assert "bill was not saved as a partial bill" in warning
    assert "do not treat an absent rate as a $0 service" in warning
    assert revenue_leakage_warning("Patient encounter was not found.") == ""


def test_athena_charge_preview_resolves_formula_fields():
    charges = _athena_charge_preview(
        [
            {
                "Payer": "Athena Health Insurance",
                "CPT/HCPCS": "85025",
                "Quantity": 2,
                "Unit Charge ($)": 10,
                "Expected Allowed ($)": 15,
                "Patient Responsibility ($)": "=P5*20%",
                "Contractual Adjustment ($)": "=O5-P5",
            },
            {"Charge Line ID": "TOTAL"},
        ]
    )

    assert len(charges) == 1
    assert charges.iloc[0]["Patient Responsibility ($)"] == 3
    assert charges.iloc[0]["Contractual Adjustment ($)"] == 5


def test_chart_review_rows_use_actual_ehr_schema_fields():
    assert _chart_review_rows(
        "Allergies",
        [{"Substance": "Penicillin", "Reaction": "Rash", "Severity": "Moderate"}],
    )[0]["Description"] == "Penicillin · Rash · Moderate"
    assert _chart_review_rows(
        "Medications",
        [{"Medication": "Metformin", "Dose": "500 mg", "Route": "Oral", "Frequency": "Daily"}],
    )[0]["Description"] == "Metformin · 500 mg · Oral · Daily"
    assert _chart_review_rows(
        "Vitals",
        [{"Systolic_mmHg": 120, "Diastolic_mmHg": 80, "Heart_Rate_bpm": 72, "SpO2_Percent": 98}],
    )[0]["Description"] == "BP: 120/80 mmHg · HR: 72 bpm · SpO2: 98%"
    assert _chart_review_rows(
        "Labs",
        [{"Test_Name": "Glucose", "Result": 95, "Unit": "mg/dL"}],
    )[0]["Description"] == "Glucose · 95 mg/dL"


def test_bill_history_hides_legacy_payers_without_changing_current_rows():
    assert is_bill_history_visible("Athena Health Insurance")
    assert is_bill_history_visible("")
    assert not is_bill_history_visible("Care Gap Health")


def test_donald_trump_documentation_generates_its_own_procedure_codes():
    documentation = (
        "A two view chest x ray was ordered to evaluate the lungs. "
        "A CT scan was ordered for a new persistent headache."
    )
    result = get_code_suggestions(
        ["Chest X-ray", "CT Scan", "Established Patient Office Visit"],
        documentation,
    )

    codes = {item["CPT/HCPCS Code"] for item in result["cpt"]}
    assert {"71046", "70450", "99213"} <= codes


def test_dwayne_transcript_generates_all_four_cpt_codes():
    transcript = (
        "The patient has fever, diabetes and asthma with productive cough and wheezing. "
        "Temperature is 101.8 F and oxygen saturation is 91%. Plan: chest X-ray, "
        "CBC, blood glucose levels, and pulse oximetry."
    )
    insights = get_record_insights({"id": "dwayne-1", "transcript": transcript})
    result = get_code_suggestions(insights, transcript)

    codes = {item["CPT/HCPCS Code"] for item in result["cpt"]}
    assert codes == {"71045", "85025", "82947", "99213"}


def test_cds_parser_extracts_reasoning_from_formatted_sections():
    result = split_cds_recommendations(
        "### Educational Decision Support for Clinician Review\n\n"
        "### Clinical Context\nDocumented fever and cough.\n\n"
        "### Reasoning\nThe documented findings require clinician review.\n\n"
        "### Evidence and Citations\nSource: Harrison, section Fever.\n\n"
        "### Safety Note\nThis is not a diagnosis."
    )

    assert "Documented fever and cough." in result["Recommendation"]
    assert "Source: Harrison, section Fever." in result["Recommendation"]
    assert "The documented findings require clinician review." in result["Reasoning"]
    assert "This is not a diagnosis." in result["Safety Note"]
