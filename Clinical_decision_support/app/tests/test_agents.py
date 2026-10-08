import pytest

from app.agent.ambient_scribe_agent import AmbientScribeAgent
from app.agent.context import EncounterContext
from app.agent.cds_agent import ClinicalDecisionSupportAgent
from app import llm_client


def test_ambient_scribe_parses_json_code_fence():
    context = AmbientScribeAgent._parse_response(
        '```json\n{"chief_complaint":"cough"}\n```'
    )

    assert context == EncounterContext(chief_complaint="cough")


def test_ambient_scribe_retries_then_builds_transcript_only_soap(monkeypatch):
    calls = 0

    def fail_generation(**kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("Model unavailable")

    monkeypatch.setattr("app.agent.ambient_scribe_agent.call_llm", fail_generation)

    context = AmbientScribeAgent().transcribe("Patient reports a mild cough.")
    from app.agent.encounter_agent import EncounterAgent

    prepared = EncounterAgent(scribe=AmbientScribeAgent()).prepare(
        "Patient reports a mild cough."
    )

    assert calls == 4
    assert context.metadata["soap_generation_status"] == "transcript_fallback"
    assert "Patient reports a mild cough." in context.hpi
    assert "Patient reports a mild cough." in prepared.soap_note
    assert "Not documented." in prepared.soap_note


def test_soap_note_falls_back_to_patient_summary_when_fields_are_empty():
    context = EncounterContext(
        patient_summary="Chief Complaint:\nFever\n\nHistory of Present Illness:\nPatient has fever.\n\nAssessment:\nPossible infection.\n\nPlan:\nOrder labs.",
    )

    note = context.as_soap_note()

    assert "Subjective:" in note
    assert "Fever" in note
    assert "Assessment:" in note
    assert "Order labs." in note


def test_soap_note_uses_model_soap_note_when_structured_fields_are_missing():
    context = EncounterContext(
        soap_note=(
            "Subjective:\nFever and cough.\n\n"
            "Objective:\nTemperature 101.8 F; oxygen saturation 91%.\n\n"
            "Assessment:\nSuspected pneumonia.\n\n"
            "Plan:\nChest X-ray and CBC."
        )
    )

    note = context.as_soap_note()
    summary = context.as_patient_summary()

    assert "Fever and cough." in note
    assert "Temperature 101.8 F; oxygen saturation 91%." in note
    assert "Suspected pneumonia." in note
    assert "Chest X-ray and CBC." in note
    assert "Objective:" in summary
    assert "Assessment:" in summary


def test_soap_note_parser_accepts_bold_markdown_headings():
    context = EncounterContext(
        patient_summary=(
            "**Subjective:** Fever for four days.\n"
            "**Objective:** Temperature 101.8 F.\n"
            "**Assessment:** Suspected infection.\n"
            "**Plan:** Order CBC."
        )
    )

    note = context.as_soap_note()

    assert "Fever for four days." in note
    assert "Temperature 101.8 F." in note
    assert "Suspected infection." in note
    assert "Order CBC." in note


def test_encounter_preparation_preserves_soap_only_scribe_output():
    from app.agent.encounter_agent import EncounterAgent

    class FakeScribe:
        name = "ambient_scribe"

        def transcribe(self, transcript):
            return EncounterContext(
                transcript=transcript,
                soap_note=(
                    "Subjective: Fever and cough.\n"
                    "Objective: Temperature 38.4 C.\n"
                    "Assessment: Acute viral syndrome.\n"
                    "Plan: Fluids and follow-up."
                ),
            )

    context = EncounterAgent(scribe=FakeScribe()).prepare(
        "Synthetic patient reports fever and cough."
    )

    assert "Fever and cough." in context.soap_note
    assert "Temperature 38.4 C." in context.soap_note
    assert "Acute viral syndrome." in context.soap_note
    assert "Fluids and follow-up." in context.soap_note


def test_cds_uses_transcript_when_patient_summary_is_empty(monkeypatch):
    captured = {}
    citation = "Source: Harrison.docx, section Cough"

    def retrieve_documents(query, top_k):
        captured["query"] = query
        return citation

    def call_llm(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return (
            "Reasoning\nThe patient reports a cough.\n\n"
            f"Evidence and citations\n{citation}"
        )

    monkeypatch.setattr("app.rag.retriever.retrieve_documents", retrieve_documents)
    monkeypatch.setattr("app.agent.cds_agent.call_llm", call_llm)

    context = ClinicalDecisionSupportAgent().analyze_context(
        EncounterContext(
            transcript="Patient reports a mild cough.",
            patient_summary="",
        )
    )

    assert captured["query"] == "Patient reports a mild cough."
    assert "Patient reports a mild cough." in captured["prompt"]
    assert "Evidence and citations" in context.recommendations
    assert citation in context.recommendations


def test_cds_retrieval_and_prompt_include_transcript_alongside_structured_note(monkeypatch):
    captured = {}
    citation = "Source: Harrison.docx, section Fever"

    def retrieve_documents(query, top_k):
        captured["query"] = query
        return citation

    def call_llm(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return f"Reasoning\nReview fever.\n\nEvidence and citations\n{citation}"

    monkeypatch.setattr("app.rag.retriever.retrieve_documents", retrieve_documents)
    monkeypatch.setattr("app.agent.cds_agent.call_llm", call_llm)

    ClinicalDecisionSupportAgent().analyze_context(
        EncounterContext(
            transcript="Patient reports fever and cough.",
            patient_summary="Assessment: Fever.",
        )
    )

    assert "Patient reports fever and cough." in captured["query"]
    assert "Assessment:\nFever." in captured["query"]
    assert "Original consultation transcript:\nPatient reports fever and cough." in captured["prompt"]
    assert "Structured clinical documentation:\nAssessment:\nFever." in captured["prompt"]


def test_cds_does_not_create_generic_recommendation_when_llm_is_empty(monkeypatch):
    monkeypatch.setattr("app.rag.retriever.retrieve_documents", lambda query, top_k: "")
    monkeypatch.setattr("app.agent.cds_agent.call_llm", lambda **kwargs: "")

    with pytest.raises(RuntimeError, match="empty response"):
        ClinicalDecisionSupportAgent().analyze_context(
            EncounterContext(patient_summary="Mild cough.")
        )


def test_encounter_agent_keeps_soap_when_cds_generation_fails():
    from app.agent.encounter_agent import EncounterAgent

    class FakeScribe:
        name = "ambient_scribe"

        def transcribe(self, transcript):
            return EncounterContext(transcript=transcript, hpi="Mild cough.")

    class FakeCds:
        name = "clinical_decision_support"

        def analyze_context(self, encounter):
            raise RuntimeError("CDS model unavailable")

    context = EncounterAgent(scribe=FakeScribe(), cds=FakeCds()).process(
        "Patient reports a mild cough."
    )

    assert "Mild cough." in context.soap_note
    assert context.recommendations == ""
    assert context.metadata["cds_generation_status"] == "unavailable"
    assert context.metadata["cds_generation_error"] == "CDS model unavailable"


def test_cds_rejects_uncited_recommendation_when_guidance_was_retrieved(monkeypatch):
    monkeypatch.setattr(
        "app.rag.retriever.retrieve_documents",
        lambda query, top_k: "Source: Harrison.docx, section Cough\nGuidance text.",
    )
    monkeypatch.setattr(
        "app.agent.cds_agent.call_llm",
        lambda **kwargs: "Reasoning\nA generic recommendation.",
    )

    with pytest.raises(RuntimeError, match="did not cite"):
        ClinicalDecisionSupportAgent().analyze_context(
            EncounterContext(patient_summary="Mild cough.")
        )


def test_cds_retries_once_when_model_omits_retrieved_citation(monkeypatch):
    citation = "Source: Harrison.docx, section Cough"
    responses = iter(
        [
            "Reasoning\nThe patient reports a cough.",
            f"Reasoning\nReview the documented cough.\n\nEvidence\n{citation}",
        ]
    )
    monkeypatch.setattr(
        "app.rag.retriever.retrieve_documents",
        lambda query, top_k: f"{citation}\nGuidance text.",
    )
    monkeypatch.setattr(
        "app.agent.cds_agent.call_llm",
        lambda **kwargs: next(responses),
    )

    context = ClinicalDecisionSupportAgent().analyze_context(
        EncounterContext(patient_summary="Mild cough.")
    )

    assert citation in context.recommendations


def test_empty_structured_context_does_not_create_blank_summary():
    assert EncounterContext().as_patient_summary() == ""


def test_call_llm_uses_cpt_api_key_and_model_overrides(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "done"}}]}

    class FakeSession:
        def mount(self, *args, **kwargs):
            pass

        def post(self, url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.llm_client.COFORGE_API_KEY", "general-key")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_API_KEY", "cpt-key")
    monkeypatch.setattr("app.llm_client.COFORGE_API_URL", "https://example.com/router")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_API_URL", "https://example.com/cpt-router")
    monkeypatch.setattr("app.llm_client.COFORGE_MODEL", "model-a")
    monkeypatch.setattr("app.llm_client.COFORGE_CPT_MODEL", "cpt-model")
    monkeypatch.setattr("app.llm_client.requests.Session", lambda: FakeSession())

    result = llm_client.call_llm_for_cpt("Find CPT code")

    assert result == "done"
    assert captured["headers"]["X-API-KEY"] == "cpt-key"
    assert captured["json"]["model"] == "cpt-model"


def test_call_llm_retries_only_generic_router_internal_error(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code, payload, text=""):
            self.status_code = status_code
            self.payload = payload
            self.text = text

        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.responses = [
                FakeResponse(
                    400,
                    {"detail": "An unexpected error occurred while generating the response."},
                    "internal error",
                ),
                FakeResponse(200, {"choices": [{"message": {"content": "done"}}]}),
            ]

        def mount(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            return self.responses.pop(0)

    monkeypatch.setattr("app.llm_client.COFORGE_API_KEY", "test-key")
    monkeypatch.setattr("app.llm_client.COFORGE_API_URL", "https://example.com/router")
    monkeypatch.setattr("app.llm_client.COFORGE_MODEL", "test-model")
    monkeypatch.setattr("app.llm_client.requests.Session", FakeSession)
    monkeypatch.setattr("app.llm_client.time.sleep", lambda _: None)

    assert llm_client.call_llm("test prompt") == "done"


def test_call_llm_does_not_retry_other_bad_requests(monkeypatch):
    class FakeResponse:
        status_code = 400
        text = "invalid model"

        @staticmethod
        def json():
            return {"detail": "The requested model is not available."}

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def mount(self, *args, **kwargs):
            pass

        def post(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    monkeypatch.setattr("app.llm_client.COFORGE_API_KEY", "test-key")
    monkeypatch.setattr("app.llm_client.COFORGE_API_URL", "https://example.com/router")
    monkeypatch.setattr("app.llm_client.COFORGE_MODEL", "test-model")
    monkeypatch.setattr("app.llm_client.requests.Session", lambda: session)

    with pytest.raises(RuntimeError, match="Coforge API error: 400"):
        llm_client.call_llm("test prompt")

    assert session.calls == 1