import requests
import pytest

from frontend.api_client import HealthcareApiClient


def test_api_client_defaults_to_project_backend_port(monkeypatch):
    monkeypatch.delenv("CLINICAL_API_URL", raising=False)

    assert HealthcareApiClient().base_url == "http://127.0.0.1:8001"


def test_regenerate_encounter_only_runs_the_scribe_stage(monkeypatch):
    captured = {}

    def fake_request_json(self, method, endpoint, payload, timeout=60):
        captured.update(
            {
                "method": method,
                "endpoint": endpoint,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return {"soap_note": "Subjective: Cough."}

    monkeypatch.setattr(HealthcareApiClient, "_request_json", fake_request_json)

    result = HealthcareApiClient().regenerate_encounter("Patient reports a cough.")

    assert result["soap_note"] == "Subjective: Cough."
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "/encounters/scribe"
    assert captured["payload"] == {
        "transcript": "Patient reports a cough.",
        "include_cds": False,
    }


class FakeResponse:
    def __init__(self, body=None, json_error=None, status_code=500):
        self.body = body
        self.json_error = json_error
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP error")

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.body


def test_request_json_reports_http_error_without_detail(monkeypatch):
    monkeypatch.setattr(
        "frontend.api_client.requests.request",
        lambda *args, **kwargs: FakeResponse({"status": "error"}),
    )

    with pytest.raises(
        RuntimeError,
        match=r"Backend returned HTTP 500 for /patients/export\.",
    ):
        HealthcareApiClient().export_patient_data()


def test_request_json_uses_backend_detail_for_http_error(monkeypatch):
    monkeypatch.setattr(
        "frontend.api_client.requests.request",
        lambda *args, **kwargs: FakeResponse({"detail": "Patient data unavailable"}),
    )

    with pytest.raises(RuntimeError, match="Patient data unavailable"):
        HealthcareApiClient().export_patient_data()


def test_request_json_falls_back_for_non_json_http_error(monkeypatch):
    monkeypatch.setattr(
        "frontend.api_client.requests.request",
        lambda *args, **kwargs: FakeResponse(json_error=ValueError("invalid JSON")),
    )

    with pytest.raises(
        RuntimeError,
        match=r"Backend returned HTTP 500 for /patients/export\.",
    ):
        HealthcareApiClient().export_patient_data()


def test_request_json_bypasses_environment_proxy_for_loopback(monkeypatch):
    request_options = {}

    def fake_request(*args, **kwargs):
        request_options.update(kwargs)
        return FakeResponse({"sheets": {}}, status_code=200)

    monkeypatch.setattr("frontend.api_client.requests.request", fake_request)

    HealthcareApiClient("http://127.0.0.1:8001").export_patient_data()

    assert request_options["proxies"] == {"http": None, "https": None}


def test_request_json_preserves_environment_proxy_for_remote_backend(monkeypatch):
    request_options = {}

    def fake_request(*args, **kwargs):
        request_options.update(kwargs)
        return FakeResponse({"sheets": {}}, status_code=200)

    monkeypatch.setattr("frontend.api_client.requests.request", fake_request)

    HealthcareApiClient("https://api.example.test").export_patient_data()

    assert request_options["proxies"] is None
