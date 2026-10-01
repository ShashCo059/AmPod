from fastapi.testclient import TestClient

from app.main import app
from app.api import routes


def test_health_check():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
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

    problems = client.put(
        f"/patients/{patient_id}/records/Problems",
        json={"records": [{"Problem_ID": "PROB-TEST", "Problem_Name": "Test problem"}]},
    )
    assert problems.json()["records"][0]["Patient_ID"] == patient_id
    assert client.get(f"/patients/{patient_id}").json()["records"]["Problems"]

    deleted = client.delete(f"/patients/{patient_id}")
    assert deleted.json() == {"deleted": True, "patient_id": patient_id}
    assert client.get(f"/patients/{patient_id}").status_code == 404
    saved = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(saved["sheets"]["Patient_Master"]["records"]) == original_patient_count


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
        lambda patient_id, icd10_codes=None, cpt_hcpcs_codes=None, encounter_id=None, encounter_type="Outpatient": generate_patient_bill(
            patient_id, icd10_codes, cpt_hcpcs_codes, encounter_id=encounter_id, encounter_type=encounter_type, data_path=data_file
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

    assert response.status_code == 200
    bill = response.json()
    assert len(bill["Line_Items"]) == 2
    assert bill["Gross_Total_USD"] == 425
    assert bill["Patient_Name"] == "John F Kennedy"
    assert bill["Encounter_ID"] == encounter["Encounter_ID"]
    assert bill["ICD_10_Codes"] == ["I10", "R50.9"]
    assert set(bill["CPT_HCPCS_Codes"]) == {"83036", "99213"}
    assert "no payer or patient-share" in bill["Notice"]
    assert all("knee" not in row["Service"].lower() for row in bill["Line_Items"])
    saved = json.loads(data_file.read_text(encoding="utf-8"))
    assert saved["sheets"]["Generated_Bills"]["records"][-1]["Patient_ID"] == patient["Patient_ID"]
    saved_bill = saved["sheets"]["Generated_Bills"]["records"][-1]
    assert saved_bill["Hospital_Account"].startswith("HAR-")
    assert saved_bill["Encounter_ID"] == encounter["Encounter_ID"]
    bill_lines = [
        line for line in saved["sheets"]["Generated_Bill_Lines"]["records"]
        if line["Bill_ID"] == saved_bill["Bill_ID"]
    ]
    assert len(bill_lines) == 2
    assert bill_lines[0]["Encounter_ID"] == saved_bill["Encounter_ID"]
    generated_claims = [
        claim for claim in saved["sheets"]["Billing_Claims"]["records"]
        if claim.get("Claim_ID", "").startswith(f"CLM-{saved_bill['Bill_ID'].removeprefix('BILL-')}-")
    ]
    assert len(generated_claims) == 2
    assert all(not claim["Payer"] and not claim["Payment_USD"] for claim in generated_claims)
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