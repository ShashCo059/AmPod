from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.api import routes


def test_health_check():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


def test_retrieve_endpoint(monkeypatch):
    monkeypatch.setattr(routes, "retrieve_documents", lambda query, top_k: "context")
    client = TestClient(app)

    response = client.post("/retrieve", json={"query": "chest pain", "top_k": 3})

    assert response.status_code == 200
    assert response.json()["context"] == "context"


def test_codes_endpoint_returns_cpt_for_treatment_procedure(monkeypatch):
    monkeypatch.setattr(routes, "get_cpt_codes", lambda procedures, threshold: [{
        "Extracted Procedure": procedures[0],
        "Matched Procedure/Service": "Physical therapy treatment",
        "CPT/HCPCS Code": "97110",
    }])
    client = TestClient(app)

    response = client.post("/codes", json={"procedures": ["Physical therapy treatment"]})

    assert response.status_code == 200
    assert response.json()["cpt"][0]["CPT/HCPCS Code"] == "97110"


def test_analyze_endpoint(monkeypatch):
    class FakeService:
        def analyze(self, patient_summary):
            return {"risk_assessment": "low"}, '{"risk_assessment":"low"}'

    monkeypatch.setattr(routes, "ClinicalAnalysisService", FakeService)
    client = TestClient(app)

    response = client.post("/analyze", json={"patient_summary": "patient summary"})

    assert response.status_code == 200
    assert response.json()["risk_assessment"] == "low"


def test_empty_patient_summary_is_rejected():
    client = TestClient(app)

    response = client.post("/analyze", json={"patient_summary": ""})

    assert response.status_code == 422


def test_encounter_endpoint(monkeypatch):
    class FakeEncounterAgent:
        def process(self, transcript):
            from app.agent.context import EncounterContext

            return EncounterContext(
                transcript=transcript,
                patient_summary="Cough reported.",
                soap_note="Subjective: Cough.",
                recommendations="Monitor symptoms.",
                metadata={"agents_invoked": ["ambient_scribe", "clinical_decision_support"]},
            )

        def prepare(self, transcript):
            return self.process(transcript)

        def add_clinical_decision_support(self, context):
            return context

    monkeypatch.setattr(routes, "get_encounter_agent", lambda: FakeEncounterAgent())
    client = TestClient(app)

    response = client.post("/encounters/analyze", json={"transcript": "Patient reports cough."})

    assert response.status_code == 200
    assert response.json()["patient_summary"] == "Cough reported."
    assert response.json()["soap_note"] == "Subjective: Cough."
    assert response.json()["metadata"]["agents_invoked"] == ["ambient_scribe", "clinical_decision_support"]


def test_cds_endpoint_uses_existing_encounter_context(monkeypatch):
    class FakeEncounterAgent:
        def add_clinical_decision_support(self, context):
            from app.agent.context import EncounterContext

            assert context.transcript == "Patient reports cough."
            assert context.patient_summary == "Cough reported."
            context.recommendations = "Monitor symptoms."
            return context

    monkeypatch.setattr(routes, "get_encounter_agent", lambda: FakeEncounterAgent())
    client = TestClient(app)

    response = client.post(
        "/encounters/cds",
        json={
            "transcript": "Patient reports cough.",
            "patient_summary": "Cough reported.",
        },
    )

    assert response.status_code == 200
    assert response.json()["recommendations"] == "Monitor symptoms."


def test_cds_endpoint_surfaces_model_failure_instead_of_returning_generic_advice(monkeypatch):
    class FakeEncounterAgent:
        def add_clinical_decision_support(self, context):
            raise RuntimeError("The CDS model returned an empty response.")

    monkeypatch.setattr(routes, "get_encounter_agent", lambda: FakeEncounterAgent())
    client = TestClient(app)

    response = client.post(
        "/encounters/cds",
        json={"transcript": "Patient reports a cough."},
    )

    assert response.status_code == 502
    assert "empty response" in response.json()["detail"]


def test_append_selected_insights_to_note_adds_values_under_heading():
    from frontend.streamlit_app import append_selected_insights_to_note

    summary = "Subjective: Cough.\n- **MAJOR DIAGNOSIS / ISSUES**\nAwaiting manual input."

    updated = append_selected_insights_to_note(summary, ["Hyperlipidemia", "Hypertension"])

    assert "Hyperlipidemia" in updated
    assert "Hypertension" in updated
    assert "- **MAJOR DIAGNOSIS / ISSUES**" in updated


def test_code_suggestions_separate_diagnoses_and_procedures():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Hypertension", "Complete blood count", "Color Blindness"])

    assert result["icd10"]
    assert result["cpt"]
    assert all("ICD-10 Code" in item for item in result["icd10"])
    assert all("CPT/HCPCS Code" in item for item in result["cpt"])


def test_hypertension_uses_general_icd10_code():
    from code_matcher import get_icd10_codes

    result = get_icd10_codes(["Hypertension"])

    assert result == [{
        "Extracted Condition": "Hypertension",
        "Matched Disease/Injury": "Essential (primary) hypertension",
        "ICD-10 Code": "I10",
    }]


def test_procedure_insights_return_cpt_codes():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Complete Blood Count"])

    assert result["icd10"] == []
    assert result["cpt"]
    assert result["cpt"][0]["CPT/HCPCS Code"] == "85025"


def test_generic_encounter_returns_office_visit_cpt_code():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Established Patient Office Visit"], "Patient evaluated.")

    assert result["cpt"]
    assert result["cpt"][0]["CPT/HCPCS Code"] == "99213"


def test_code_suggestions_route_office_visit_to_cpt():
    from frontend.streamlit_app import get_code_suggestions

    result = get_code_suggestions(["Established Patient Office Visit"], "Patient evaluated.")

    assert result["icd10"] == []
    assert any(item["CPT/HCPCS Code"] == "99213" for item in result["cpt"])


def test_codes_endpoint_returns_office_visit_cpt_code():
    client = TestClient(app)

    response = client.post(
        "/codes",
        json={"procedures": ["Established Patient Office Visit"]},
    )

    assert response.status_code == 200
    assert response.json()["cpt"][0]["CPT/HCPCS Code"] == "99213"


def test_physical_therapy_is_detected_as_a_cpt_insight():
    from frontend.streamlit_app import get_detected_insights

    assert "Physical Therapy" in get_detected_insights("Begin physical therapy twice weekly.")


def test_planned_a1c_returns_cpt_code():
    from code_matcher import get_cpt_candidate_sets

    results = get_cpt_candidate_sets(["A1C Test"], "Completing an hba1c test is recommended.")

    assert results[0]["selected_code"] == "83036"
    assert results[0]["status"] == "suggested"


def test_ordered_head_ct_and_two_view_chest_xray_use_specific_cpt_codes():
    from code_matcher import get_cpt_candidate_sets

    results = get_cpt_candidate_sets(
        ["CT scan of head without contrast", "Chest x ray 2 views"],
        "I am ordering a two view chest X-ray and a CT scan of the head without contrast.",
    )

    assert [item["selected_code"] for item in results] == ["70450", "71046"]


def test_codes_endpoint_forces_common_a1c_and_two_view_xray_codes():
    client = TestClient(app)
    response = client.post(
        "/codes",
        json={
            "documentation": "Complete hba1c test and order a to view chest X-ray.",
        },
    )

    assert response.status_code == 200
    codes = {item["CPT/HCPCS Code"] for item in response.json()["cpt"]}
    assert {"83036", "71046"}.issubset(codes)


def test_patient_endpoints_create_edit_and_delete_linked_demo_data(monkeypatch, tmp_path):
    import json

    from app.services import ehr_data_service

    data_file = tmp_path / "patients.json"
    data_file.write_text(ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(routes, "list_patients", lambda: ehr_data_service.list_patients(data_file))
    monkeypatch.setattr(routes, "get_patient_chart", lambda patient_id: ehr_data_service.get_patient_chart(patient_id, data_file))
    monkeypatch.setattr(routes, "create_patient", lambda values: ehr_data_service.create_patient(values, data_file))
    monkeypatch.setattr(routes, "update_patient", lambda patient_id, values: ehr_data_service.update_patient(patient_id, values, data_file))
    monkeypatch.setattr(
        routes,
        "replace_patient_records",
        lambda patient_id, sheet_name, records: ehr_data_service.replace_patient_records(patient_id, sheet_name, records, data_file),
    )
    monkeypatch.setattr(routes, "delete_patient", lambda patient_id: ehr_data_service.delete_patient(patient_id, data_file))
    client = TestClient(app)

    original_patients = client.get("/patients").json()["patients"]
    original_patient_count = len(original_patients)
    original_patient_ids = {patient["Patient_ID"] for patient in original_patients}
    created = client.post(
        "/patients",
        json={"values": {"First_Name": "Avery", "Last_Name": "Example", "DOB": "1985-02-03"}},
    )
    assert created.status_code == 200
    patient_id = created.json()["Patient_ID"]
    assert patient_id not in original_patient_ids

    updated = client.put(
        f"/patients/{patient_id}",
        json={"values": {"First_Name": "Alex", "City": "Denver"}},
    )
    assert updated.json()["Legal_Name"] == "Alex Example"
    assert updated.json()["City"] == "Denver"
    audit_after_demographics = client.get(f"/patients/{patient_id}").json()["records"]["Audit_Log"]
    assert audit_after_demographics[-1]["Record_Type"] == "Patient_Master"
    assert json.loads(audit_after_demographics[-1]["Before"])["Legal_Name"] == "Avery Example"
    assert json.loads(audit_after_demographics[-1]["After"])["Legal_Name"] == "Alex Example"

    problems = client.put(
        f"/patients/{patient_id}/records/Problems",
        json={"records": [{"Problem_ID": "PROB-TEST", "Problem_Name": "Test problem"}]},
    )
    assert problems.json()["records"][0]["Patient_ID"] == patient_id
    updated_chart = client.get(f"/patients/{patient_id}").json()
    assert updated_chart["records"]["Problems"]
    audit_after_problems = updated_chart["records"]["Audit_Log"]
    assert audit_after_problems[-1]["Record_Type"] == "Problems"
    assert json.loads(audit_after_problems[-1]["Before"]) == []
    assert json.loads(audit_after_problems[-1]["After"])[0]["Problem_Name"] == "Test problem"

    tamper_attempt = client.put(
        f"/patients/{patient_id}/records/Audit_Log",
        json={"records": []},
    )
    assert tamper_attempt.status_code == 422

    deleted = client.delete(f"/patients/{patient_id}")
    assert deleted.json() == {"deleted": True, "patient_id": patient_id}
    assert client.get(f"/patients/{patient_id}").status_code == 404
    saved = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(saved["sheets"]["Patient_Master"]["records"]) == original_patient_count


def test_patient_bmi_is_calculated_from_height_and_weight(tmp_path):
    from app.services import ehr_data_service

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    patient = next(
        row
        for row in ehr_data_service.get_patient_chart("P100001", data_file)["records"][
            "Patient_Master"
        ]
    )
    assert patient["BMI"] == 22.03

    updated = ehr_data_service.update_patient(
        "P100001",
        {"Height_cm": 160},
        data_file,
    )
    assert updated["BMI"] == 21.48
    assert ehr_data_service.get_patient_chart("P100001", data_file)["records"][
        "Patient_Master"
    ][0]["BMI"] == 21.48

    invalid_update = ehr_data_service.update_patient(
        "P100001",
        {"Height_cm": 0},
        data_file,
    )
    assert invalid_update["BMI"] == ""

    created = ehr_data_service.create_patient(
        {
            "First_Name": "BMI",
            "Last_Name": "Example",
            "DOB": "1985-02-03",
            "Height_cm": 170,
            "Weight_kg": 70,
            "BMI": '=IFERROR(R2/((Q2/100)^2),"")',
        },
        data_file,
    )
    assert created["BMI"] == 24.22


def test_generate_ipd_bill_uses_gross_charges_and_persists_to_patient_json(monkeypatch, tmp_path):
    import json

    from app.services import ehr_data_service
    from app.services.billing_service import generate_patient_bill, save_generated_bill

    data_file = tmp_path / "patients.json"
    data_file.write_text(ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    patient = ehr_data_service.create_patient(
        {"First_Name": "John", "Last_Name": "F Kennedy", "DOB": "1956-05-29"},
        data_file,
    )
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Office Visit", "Status": "Completed", "Date": "2026-10-01"},
        data_file,
    )
    template_encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Inpatient Reference Billing", "Status": "Billing Draft"},
        data_file,
    )
    payload = ehr_data_service.load_ehr_data(data_file)
    payload["sheets"].setdefault(
        "Generated_Bills",
        {"structure": "records", "headers": [], "records": []},
    )["records"].append(
        {
            "Bill_ID": "BILL-OLD-TEMPLATE",
            "Patient_ID": patient["Patient_ID"],
            "Encounter_ID": template_encounter["Encounter_ID"],
            "Source_Sheet": "IPD Encounter + CDM Master",
            "Template_Encounter": "CSN-TEMPLATE",
            "Bill_Status": "Draft",
            "Line_Count": 1,
            "Gross_Total_USD": 20000,
        }
    )
    payload["sheets"].setdefault(
        "Generated_Bill_Lines",
        {"structure": "records", "headers": [], "records": []},
    )["records"].append(
        {
            "Bill_Line_ID": "BILL-OLD-TEMPLATE-L001",
            "Bill_ID": "BILL-OLD-TEMPLATE",
            "Patient_ID": patient["Patient_ID"],
            "Encounter_ID": template_encounter["Encounter_ID"],
            "Service": "Cemented Knee Implant Package",
            "Gross_Charge_USD": 20000,
        }
    )
    payload["sheets"]["Billing_Claims"]["records"].append(
        {
            "Claim_ID": "CLM-OLD-TEMPLATE-001",
            "Patient_ID": patient["Patient_ID"],
            "Encounter_ID": template_encounter["Encounter_ID"],
            "Description": "Cemented Knee Implant Package",
            "Charge_USD": 20000,
            "Claim_Status": "Draft",
            "Notes": "",
        }
    )
    ehr_data_service.save_ehr_data(payload, data_file)
    monkeypatch.setattr(
        routes,
        "generate_patient_bill",
        lambda patient_id, icd10_codes=None, cpt_hcpcs_codes=None, encounter_id=None, encounter_type="Outpatient", payer_name=None: generate_patient_bill(
            patient_id, icd10_codes, cpt_hcpcs_codes, encounter_id=encounter_id, encounter_type=encounter_type, data_path=data_file, payer_name=payer_name
        ),
    )
    monkeypatch.setattr(routes, "save_generated_bill", lambda bill: save_generated_bill(bill, data_file))
    client = TestClient(app)

    response = client.post(
        f"/patients/{patient['Patient_ID']}/bills/ipd",
        json={
            "icd10_codes": ["I10", "R50.9"],
            "cpt_hcpcs_codes": ["83036", "99213"],
            "encounter_id": encounter["Encounter_ID"],
            "encounter_type": "Outpatient",
        },
    )

    assert response.status_code == 200, response.text
    bill = response.json()
    first_bill_id = bill["Bill_ID"]
    first_hospital_account = bill["Hospital_Account"]
    assert len(bill["Line_Items"]) == 2
    assert bill["Gross_Total_USD"] == 425
    assert bill["Patient_Name"] == "John F Kennedy"
    assert bill["Encounter_ID"] == encounter["Encounter_ID"]
    assert bill["ICD_10_Codes"] == ["I10", "R50.9"]
    assert set(bill["CPT_HCPCS_Codes"]) == {"83036", "99213"}
    assert bill["Payer_Name"] == "Athena Health Insurance"
    assert "Synthetic Athena Health Insurance allowed" in bill["Notice"]
    assert all("knee" not in row["Service"].lower() for row in bill["Line_Items"])
    second_response = client.post(
        f"/patients/{patient['Patient_ID']}/bills/ipd",
        json={
            "icd10_codes": ["I10", "R50.9"],
            "cpt_hcpcs_codes": ["83036", "99213"],
            "encounter_id": encounter["Encounter_ID"],
            "encounter_type": "Outpatient",
        },
    )
    assert second_response.status_code == 409
    assert "already exists" in second_response.json()["detail"]

    saved = json.loads(data_file.read_text(encoding="utf-8"))
    assert saved["sheets"]["Generated_Bills"]["records"][-1]["Patient_ID"] == patient["Patient_ID"]
    saved_bill = saved["sheets"]["Generated_Bills"]["records"][-1]
    assert saved_bill["Hospital_Account"] == first_hospital_account
    assert saved_bill["Encounter_ID"] == encounter["Encounter_ID"]
    encounter_account = next(
        row["Hospital_Account"]
        for row in saved["sheets"]["Encounters"]["records"]
        if row["Encounter_ID"] == encounter["Encounter_ID"]
    )
    assert encounter_account == first_hospital_account
    encounter_bills = [
        row for row in saved["sheets"]["Generated_Bills"]["records"]
        if row.get("Encounter_ID") == encounter["Encounter_ID"]
        and row.get("Patient_ID") == patient["Patient_ID"]
    ]
    assert len(encounter_bills) == 1
    assert encounter_bills[0]["Bill_ID"] == first_bill_id
    assert encounter_bills[0]["Bill_Status"] == "Draft"
    assert encounter_bills[0]["Hospital_Account"] == first_hospital_account
    bill_lines = [
        line for line in saved["sheets"]["Generated_Bill_Lines"]["records"]
        if line["Bill_ID"] == saved_bill["Bill_ID"]
    ]
    assert len(bill_lines) == 2
    assert bill_lines[0]["Encounter_ID"] == saved_bill["Encounter_ID"]
    assert all(line["Line_Status"] == "Draft" for line in bill_lines)
    generated_claims = [
        claim for claim in saved["sheets"]["Billing_Claims"]["records"]
        if claim.get("Claim_ID", "").startswith(f"CLM-{saved_bill['Bill_ID'].removeprefix('BILL-')}-")
    ]
    assert len(generated_claims) == 2
    assert all(claim["Payer"] == "Athena Health Insurance" for claim in generated_claims)
    assert all(not claim["Payment_USD"] for claim in generated_claims)
    assert ehr_data_service.get_patient_chart(
        patient["Patient_ID"], data_file
    )["records"]["Generated_Bills"][-1]["Bill_ID"] == saved_bill["Bill_ID"]
    bill_audit = saved["sheets"]["Audit_Log"]["records"][-1]
    assert bill_audit["Action"] == "Draft bill generated"
    assert json.loads(bill_audit["After"])["Bill_ID"] == saved_bill["Bill_ID"]
    old_bill = next(
        row for row in saved["sheets"]["Generated_Bills"]["records"]
        if row["Bill_ID"] == "BILL-OLD-TEMPLATE"
    )
    old_line = next(
        row for row in saved["sheets"]["Generated_Bill_Lines"]["records"]
        if row["Bill_ID"] == "BILL-OLD-TEMPLATE"
    )
    old_claim = next(
        row for row in saved["sheets"]["Billing_Claims"]["records"]
        if row["Claim_ID"] == "CLM-OLD-TEMPLATE-001"
    )
    assert old_bill["Bill_Status"] == "Superseded"
    assert old_line["Line_Status"] == "Voided"
    assert old_claim["Claim_Status"] == "Void"


def test_demo_and_new_patients_use_athena_as_the_only_payer():
    from collections import Counter

    from app.services.ehr_data_service import list_patients

    patients = list_patients()

    assert len(patients) >= 52
    assert Counter(patient["Payer_Name"] for patient in patients) == {
        "Athena Health Insurance": len(patients)
    }
    assert (patients[50]["Patient_ID"], patients[50]["Legal_Name"]) == (
        "P100051",
        "John F Kennedy",
    )
    assert (patients[51]["Patient_ID"], patients[51]["Legal_Name"]) == (
        "P100052",
        "Donald Trump",
    )


def test_payer_selected_bill_uses_payer_allowed_amount_and_persists_selection(
    monkeypatch, tmp_path
):
    import json

    from app.services import ehr_data_service
    from app.services.billing_service import generate_patient_bill, save_generated_bill

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    patient = ehr_data_service.create_patient(
        {
            "First_Name": "Payer",
            "Last_Name": "Test",
            "DOB": "1980-04-23",
        },
        path=data_file,
    )
    assert next(
        row for row in ehr_data_service.list_patients(data_file)
        if row["Patient_ID"] == patient["Patient_ID"]
    )["Payer_Name"] == "Athena Health Insurance"
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Office Visit", "Status": "Open", "Date": "2026-10-06"},
        path=data_file,
    )

    def generate_for_test(
        patient_id,
        icd10_codes=None,
        cpt_hcpcs_codes=None,
        encounter_id=None,
        encounter_type="Outpatient",
        payer_name=None,
    ):
        return generate_patient_bill(
            patient_id,
            icd10_codes,
            cpt_hcpcs_codes,
            encounter_id=encounter_id,
            encounter_type=encounter_type,
            data_path=data_file,
            payer_name=payer_name,
        )

    monkeypatch.setattr(routes, "generate_patient_bill", generate_for_test)
    monkeypatch.setattr(
        routes,
        "save_generated_bill",
        lambda bill: save_generated_bill(bill, data_file),
    )
    client = TestClient(app)

    def generate_for_payer(payer_name):
        return client.post(
            f"/patients/{patient['Patient_ID']}/bills/ipd",
            json={
                "cpt_hcpcs_codes": ["99213"],
                "encounter_id": encounter["Encounter_ID"],
                "encounter_type": "Outpatient",
                "payer_name": payer_name,
            },
        )

    athena_response = generate_for_payer("Athena Health Insurance")
    assert athena_response.status_code == 200, athena_response.text
    assert next(
        row for row in ehr_data_service.list_patients(data_file)
        if row["Patient_ID"] == patient["Patient_ID"]
    )["Payer_Name"] == "Athena Health Insurance"

    unsupported_response = generate_for_payer("Care Gap Health")
    assert unsupported_response.status_code == 422
    athena_bill = athena_response.json()
    assert athena_bill["Payer_Name"] == "Athena Health Insurance"
    assert athena_bill["Gross_Total_USD"] == 315
    assert athena_bill["Expected_Allowed_Total_USD"] == 187.9
    assert athena_bill["Line_Items"][0]["Payer"] == "Athena Health Insurance"

    saved = json.loads(data_file.read_text(encoding="utf-8"))
    generated_bills = saved["sheets"]["Generated_Bills"]["records"]
    created_bills = [
        bill for bill in generated_bills
        if bill.get("Patient_ID") == patient["Patient_ID"]
    ]
    assert [bill["Payer_Name"] for bill in created_bills] == [
        "Athena Health Insurance"
    ]
    active_coverage = [
        row
        for row in saved["sheets"]["Insurance"]["records"]
        if row.get("Patient_ID") == patient["Patient_ID"]
        and row.get("Status", "Active") == "Active"
    ]
    assert len(active_coverage) == 1
    assert active_coverage[0]["Payer_Name"] == "Athena Health Insurance"
    claims = [
        claim for claim in saved["sheets"]["Billing_Claims"]["records"]
        if claim.get("Patient_ID") == patient["Patient_ID"]
    ]
    assert {claim["Payer"] for claim in claims} == {"Athena Health Insurance"}
    assert all(claim["Charge_USD"] == 315 for claim in claims)
    assert {claim["Expected_Allowed_USD"] for claim in claims} == {187.9}


def test_donald_trump_procedure_bundle_uses_athena_rates(tmp_path):
    from app.services import ehr_data_service
    from app.services.billing_service import generate_patient_bill

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    patient = ehr_data_service.create_patient(
        {
            "First_Name": "Donald",
            "Last_Name": "Trump",
            "DOB": "1950-09-09",
        },
        path=data_file,
    )
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Office Visit", "Status": "Open", "Date": "2026-10-06"},
        path=data_file,
    )
    cpt_codes = ["70450", "99213", "71046"]

    athena_bill = generate_patient_bill(
        patient["Patient_ID"],
        cpt_hcpcs_codes=cpt_codes,
        encounter_id=encounter["Encounter_ID"],
        data_path=data_file,
        payer_name="Athena Health Insurance",
    )
    assert athena_bill["CPT_HCPCS_Codes"] == cpt_codes
    assert athena_bill["Unpriced_Codes"] == []


def test_four_code_athena_bundle_uses_athena_only_json_rates(tmp_path):
    from app.services import ehr_data_service
    from app.services.billing_service import generate_patient_bill

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    patient = ehr_data_service.create_patient(
        {
            "First_Name": "Dwayne",
            "Last_Name": "Test",
            "DOB": "1990-01-01",
        },
        path=data_file,
    )
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Office Visit", "Status": "Open", "Date": "2026-10-07"},
        path=data_file,
    )
    cpt_codes = ["85025", "82947", "71045", "99213"]

    bill = generate_patient_bill(
        patient["Patient_ID"],
        cpt_hcpcs_codes=cpt_codes,
        encounter_id=encounter["Encounter_ID"],
        data_path=data_file,
        payer_name="Athena Health Insurance",
    )

    assert bill["Line_Count"] == 4
    assert bill["CPT_HCPCS_Codes"] == cpt_codes
    assert bill["Unpriced_Codes"] == []
    assert {line["CPT/HCPCS"] for line in bill["Line_Items"]} == set(cpt_codes)
    assert all(line["Payer"] == "Athena Health Insurance" for line in bill["Line_Items"])
    assert bill["Source_File"] == "Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json"
    rates = {
        line["CPT/HCPCS"]: (
            line["Unit Charge (USD)"],
            line["Expected Allowed (USD)"],
            line["Patient Responsibility (USD)"],
        )
        for line in bill["Line_Items"]
    }
    assert rates["71045"] == (140, 61.78, 12.36)
    assert rates["82947"] == (45, 22.11, 4.42)
    assert bill["Gross_Total_USD"] == 655
    assert bill["Expected_Allowed_Total_USD"] == 347.96
    assert bill["Patient_Responsibility_Total_USD"] == 69.59
    assert bill["Encounter_Type"] == "Outpatient"


def test_encounter_order_and_consultation_code_do_not_double_bill_ecg(
    monkeypatch, tmp_path
):
    from app.services import billing_service, ehr_data_service

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    patient = ehr_data_service.create_patient(
        {"First_Name": "ECG", "Last_Name": "Example", "DOB": "1985-02-03"},
        path=data_file,
    )
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Outpatient", "Status": "Open", "Date": "2026-10-08"},
        path=data_file,
    )
    ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Orders_Procedures",
        {
            "Encounter_ID": encounter["Encounter_ID"],
            "CPT_HCPCS": "93000",
            "Quantity": 1,
            "Order_Description": "Electrocardiogram",
        },
        path=data_file,
    )
    monkeypatch.setattr(
        billing_service,
        "_read_sheet",
        lambda _source, _sheet: [
            {
                "CPT/HCPCS": "93000",
                "Gross Unit Charge ($)": 125,
                "Status": "Active",
                "EAP/CDM Charge Code": "ECG-001",
                "Charge Description": "Electrocardiogram",
                "Revenue Code": "0730",
                "Unit Basis": "Per service",
                "Department": "Cardiology",
            }
        ],
    )

    bill = billing_service.generate_patient_bill(
        patient["Patient_ID"],
        cpt_hcpcs_codes=["93000", "93000"],
        encounter_id=encounter["Encounter_ID"],
        data_path=data_file,
    )

    assert bill["Line_Count"] == 1
    assert bill["CPT_HCPCS_Codes"] == ["93000"]
    assert bill["Line_Items"][0]["Quantity"] == 1
    assert bill["Gross_Total_USD"] == 125


def test_seed_demo_dataset_has_consistent_references_and_bill_lines():
    from app.services.ehr_data_service import load_ehr_data

    sheets = load_ehr_data()["sheets"]
    patient_ids = {
        row["Patient_ID"]
        for row in sheets["Patient_Master"]["records"]
    }
    encounter_ids = {
        row["Encounter_ID"]
        for row in sheets["Encounters"]["records"]
    }
    patients = sheets["Patient_Master"]["records"]
    assert len(patient_ids) == len(patients)
    assert all(isinstance(patient.get("Age"), int) for patient in patients)
    for patient_id in patient_ids:
        assert sum(
            row.get("Patient_ID") == patient_id
            and row.get("Status", "Active") == "Active"
            and row.get("Priority") == "Primary"
            for row in sheets["Insurance"]["records"]
        ) == 1
    bills = {
        row["Bill_ID"]: row
        for row in sheets["Generated_Bills"]["records"]
    }
    assert len(bills) == len(sheets["Generated_Bills"]["records"])

    for sheet in sheets.values():
        for row in sheet.get("records", []):
            if row.get("Patient_ID"):
                assert row["Patient_ID"] in patient_ids
            if row.get("Encounter_ID"):
                assert row["Encounter_ID"] in encounter_ids

    for line in sheets["Generated_Bill_Lines"]["records"]:
        assert line["Bill_ID"] in bills
        assert line["Patient_ID"] == bills[line["Bill_ID"]]["Patient_ID"]

    for bill in bills.values():
        lines = [
            line
            for line in sheets["Generated_Bill_Lines"]["records"]
            if line["Bill_ID"] == bill["Bill_ID"]
        ]
        assert len(lines) == int(bill.get("Line_Count") or 0)
        line_total = round(
            sum(float(line.get("Gross_Charge_USD") or 0) for line in lines),
            2,
        )
        assert abs(line_total - float(bill.get("Gross_Total_USD") or 0)) <= 0.01


def test_bill_generation_rejects_a_partially_priced_cpt_bundle(monkeypatch, tmp_path):
    from app.services import ehr_data_service
    from app.services import billing_service

    data_file = tmp_path / "patients.json"
    data_file.write_text(
        ehr_data_service.DEMO_DATA_FILE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    patient = ehr_data_service.create_patient(
        {
            "First_Name": "Dwayne",
            "Last_Name": "Test",
            "DOB": "1990-01-01",
        },
        path=data_file,
    )
    encounter = ehr_data_service.create_patient_record(
        patient["Patient_ID"],
        "Encounters",
        {"Type": "Office Visit", "Status": "Open", "Date": "2026-10-07"},
        path=data_file,
    )
    monkeypatch.setattr(
        billing_service,
        "_read_sheet",
        lambda source, sheet: [
            {
                "Payer": "Athena Health Insurance",
                "Charge Status": "Posted",
                "Quantity": 1,
                "CPT/HCPCS": "85025",
                "Unit Charge ($)": 155,
                "Expected Allowed ($)": 76.17,
                "Patient Responsibility ($)": 15.234,
                "Contractual Adjustment ($)": 78.83,
                "Charge Code": "CBC-OP",
                "Charge Description": "Complete blood count",
                "Revenue Code": "0300",
                "Department": "Laboratory",
            }
        ],
    )

    with pytest.raises(ValueError, match="no Athena Health Insurance rate.*82947, 71045"):
        billing_service.generate_patient_bill(
            patient["Patient_ID"],
            cpt_hcpcs_codes=["85025", "82947", "71045", "99213"],
            encounter_id=encounter["Encounter_ID"],
            data_path=data_file,
            payer_name="Athena Health Insurance",
        )


def test_create_patient_linked_record_endpoint(monkeypatch):
    monkeypatch.setattr(
        routes,
        "create_patient_record",
        lambda patient_id, sheet_name, values: {
            "Patient_ID": patient_id,
            "Encounter_ID": "ENC00999",
            **values,
        },
    )
    client = TestClient(app)

    response = client.post(
        "/patients/P100051/records/Encounters",
        json={"values": {"Type": "Inpatient", "Status": "Open"}},
    )

    assert response.status_code == 200
    assert response.json()["Patient_ID"] == "P100051"
    assert response.json()["Type"] == "Inpatient"


def test_manual_consultation_registers_epic_patient_encounter_and_note():
    from frontend.streamlit_app import register_consultation_in_ehr

    class FakeClient:
        def __init__(self):
            self.patient_values = None
            self.created_records = []

        def create_patient(self, values):
            self.patient_values = values
            return {
                "Patient_ID": "P100051",
                "MRN": "MRN500051",
                "Legal_Name": "Casey Rivera",
                "DOB": values["DOB"],
                "Age": 38,
            }

        def create_patient_record(self, patient_id, sheet_name, values):
            row = {"Patient_ID": patient_id, **values}
            row["Encounter_ID" if sheet_name == "Encounters" else "Note_ID"] = (
                "ENC00504" if sheet_name == "Encounters" else "NOTE00051"
            )
            self.created_records.append((sheet_name, row))
            return row

    client = FakeClient()
    record = {
        "name": "Casey Rivera",
        "age": "38",
        "gender": "Female",
        "dob": "1988-04-23",
        "date": "01/10/2026",
        "doctor": "Morgan Lee, MD",
        "status": "Pending",
        "summary": "Patient reports knee pain.",
    }

    registered = register_consultation_in_ehr(record, client=client, dob=record["dob"])

    assert client.patient_values["DOB"] == "1988-04-23"
    assert registered["patient_id"] == "P100051"
    assert registered["ehr_encounter_id"] == "ENC00504"
    assert registered["ehr_note_id"] == "NOTE00051"
    assert client.created_records[0][1]["Patient_ID"] == "P100051"
    assert client.created_records[1][1]["Encounter_ID"] == "ENC00504"
    assert client.created_records[1][1]["Service_Date"] == "2026-10-01"


def test_consultation_name_validation_requires_first_and_last_name():
    from frontend.streamlit_app import has_first_and_last_name

    assert not has_first_and_last_name("john")
    assert not has_first_and_last_name("  ")
    assert has_first_and_last_name("John Doe")
    assert has_first_and_last_name("Mary Jane Watson")


def test_consultation_registration_rejects_one_word_name_before_ehr_write():
    from frontend.streamlit_app import register_consultation_in_ehr

    class FakeClient:
        def create_patient(self, values):
            raise AssertionError("A patient must not be created with an incomplete name.")

    with pytest.raises(ValueError, match="first and last name"):
        register_consultation_in_ehr(
            {"name": "john", "age": "50"},
            client=FakeClient(),
        )


def test_consultation_database_uses_project_path_from_any_working_directory(
    monkeypatch,
    tmp_path,
):
    import frontend.streamlit_app as streamlit_app

    assert streamlit_app.DB_FILE == streamlit_app.PROJECT_ROOT / "clinical_records.json"
    assert streamlit_app.DB_FILE.is_absolute()

    database_path = tmp_path / "clinical_records.json"
    working_directory = tmp_path / "different-working-directory"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)
    monkeypatch.setattr(streamlit_app, "DB_FILE", database_path)

    records = [{"id": "PT-TEST", "name": "Test Patient"}]
    streamlit_app.save_db(records)

    assert streamlit_app.load_db() == records
    assert database_path.exists()