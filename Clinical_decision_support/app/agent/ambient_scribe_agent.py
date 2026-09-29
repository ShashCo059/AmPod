import json
from typing import Any

from app.agent.context import EncounterContext
from app.llm_client import call_llm


class AmbientScribeAgent:
    """Convert a clinical transcript into structured encounter documentation."""

    name = "ambient_scribe"

    def transcribe(self, transcript: str) -> EncounterContext:
        response = call_llm(
            prompt=(
                "Convert this doctor-patient transcript into structured clinical documentation. "
                "Return only valid JSON with string fields: "
                '{"soap_note":"", "patient_summary":"", "key_highlights":[], "chief_complaint":"", "hpi":"", '
                '"past_medical_history":"", "current_medications":"", "objective":"", "assessment":"", "plan":""}\n\n'
                f"Transcript:\n{transcript}"
            ),
            system_prompt=(
                "You are an ambient clinical scribe. Extract only information stated in the transcript. "
                "Do not invent diagnoses, medications, or treatment decisions. Return JSON only."
            ),
            temperature=0.2,
        )
        return self._parse_response(response, transcript)

    @staticmethod
    def _parse_response(response: str, transcript: str = "") -> EncounterContext:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```json").removesuffix("```").strip()

        data: dict[str, Any] = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("Ambient scribe response must be a JSON object.")
        data["key_highlights"] = data.get("key_highlights", [])
        return EncounterContext(transcript=transcript, **data)