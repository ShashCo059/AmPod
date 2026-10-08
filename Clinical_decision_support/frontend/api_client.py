import ipaddress
import os
from urllib.parse import urlsplit

import requests


class HealthcareApiClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url or os.getenv("CLINICAL_API_URL") or "http://127.0.0.1:8001"
        ).rstrip("/")

    def analyze_encounter(self, transcript: str) -> dict:
        return self._post_encounter("/encounters/analyze", transcript, include_cds=True)

    def regenerate_encounter(self, transcript: str) -> dict:
        return self.scribe_encounter(transcript)

    def regenerate_cds(self, encounter: dict) -> dict:
        return self.add_cds(encounter)

    def scribe_encounter(self, transcript: str) -> dict:
        return self._post_encounter("/encounters/scribe", transcript, include_cds=False)

    def add_cds(self, encounter: dict) -> dict:
        for attempt in range(2):
            result = self._request_json(
                "POST",
                "/encounters/cds",
                encounter,
                timeout=180,
            )
            recommendation = result.get("recommendations")
            if recommendation and str(recommendation).strip():
                return result

            if attempt == 0:
                continue

        raise RuntimeError("The CDS service returned no recommendation after retrying.")

    def match_codes(self, conditions: list[str], procedures: list[str], documentation: str = "") -> dict:
        return self._request_json(
            "POST",
            "/codes",
            {
                "conditions": conditions,
                "procedures": procedures,
                "documentation": documentation,
            },
        )

    def list_patients(self) -> list[dict]:
        return self._request_json("GET", "/patients").get("patients", [])

    def export_patient_data(self) -> dict:
        return self._request_json("GET", "/patients/export")

    def get_patient_chart(self, patient_id: str) -> dict:
        return self._request_json("GET", f"/patients/{patient_id}")

    def create_patient(self, values: dict) -> dict:
        return self._request_json("POST", "/patients", {"values": values})

    def update_patient(self, patient_id: str, values: dict) -> dict:
        return self._request_json("PUT", f"/patients/{patient_id}", {"values": values})

    def replace_patient_records(self, patient_id: str, sheet_name: str, records: list[dict]) -> dict:
        return self._request_json(
            "PUT",
            f"/patients/{patient_id}/records/{sheet_name}",
            {"records": records},
        )

    def create_patient_record(self, patient_id: str, sheet_name: str, values: dict) -> dict:
        return self._request_json(
            "POST",
            f"/patients/{patient_id}/records/{sheet_name}",
            {"values": values},
        )

    def delete_patient(self, patient_id: str) -> dict:
        return self._request_json("DELETE", f"/patients/{patient_id}")

    def generate_patient_bill(
        self,
        patient_id: str,
        icd10_codes: list[str],
        cpt_hcpcs_codes: list[str],
        encounter_id: str | None = None,
        encounter_type: str = "Outpatient",
        payer_name: str | None = None,
    ) -> dict:
        payload = {
            "icd10_codes": icd10_codes,
            "cpt_hcpcs_codes": cpt_hcpcs_codes,
            "encounter_id": encounter_id,
            "encounter_type": encounter_type,
        }
        if payer_name:
            payload["payer_name"] = payer_name
        return self._request_json("POST", f"/patients/{patient_id}/bills/ipd", payload)

    def _request_json(
        self,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        timeout: int = 60,
    ) -> dict:
        url = f"{self.base_url}{endpoint}"
        hostname = urlsplit(self.base_url).hostname
        if hostname and hostname.lower() == "localhost":
            proxies = {"http": None, "https": None}
        else:
            try:
                is_loopback = bool(hostname and ipaddress.ip_address(hostname).is_loopback)
            except ValueError:
                is_loopback = False
            proxies = {"http": None, "https": None} if is_loopback else None

        try:
            response = requests.request(
                method,
                url,
                json=payload,
                timeout=timeout,
                proxies=proxies,
            )
        except requests.exceptions.ConnectionError as error:
            raise RuntimeError(
                f"Cannot connect to the FastAPI backend at {self.base_url}. "
                "Start it with: python -m app"
            ) from error
        except requests.exceptions.Timeout as error:
            raise RuntimeError(f"The FastAPI backend timed out while requesting {endpoint}.") from error
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as error:
            try:
                error_body = response.json()
            except ValueError:
                error_body = None
            detail = error_body.get("detail") if isinstance(error_body, dict) else None
            message = (
                str(detail)
                if detail
                else f"Backend returned HTTP {response.status_code} for {endpoint}."
            )
            raise RuntimeError(message) from error
        return response.json()

    def _post_encounter(self, endpoint: str, transcript: str, include_cds: bool) -> dict:
        return self._request_json(
            "POST",
            endpoint,
            {"transcript": transcript, "include_cds": include_cds},
            timeout=180,
        )
