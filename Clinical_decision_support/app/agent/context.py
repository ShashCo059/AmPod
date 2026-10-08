import re
from typing import Any

from pydantic import BaseModel, Field


NOTE_HEADINGS = {
    "subjective": "subjective",
    "chief complaint": "chief_complaint",
    "history of present illness": "hpi",
    "hpi": "hpi",
    "past medical history": "past_medical_history",
    "pmh": "past_medical_history",
    "current medications": "current_medications",
    "medications": "current_medications",
    "objective": "objective",
    "assessment": "assessment",
    "plan": "plan",
}


def _parse_note_sections(text: str) -> dict[str, str]:
    heading_pattern = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*|__)?"
        r"(Subjective|Chief Complaint|History of Present Illness|HPI|"
        r"Past Medical History|PMH|Current Medications|Medications|"
        r"Objective|Assessment|Plan)"
        r"(?:\*\*|__)?\s*:?\s*(.*?)\s*$",
        re.IGNORECASE,
    )
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def save_current() -> None:
        if current_key and any(line.strip() for line in current_lines):
            value = "\n".join(current_lines).strip()
            if sections.get(current_key):
                sections[current_key].append(value)
            else:
                sections[current_key] = [value]

    for line in text.splitlines():
        match = heading_pattern.match(line)
        if match:
            save_current()
            current_key = NOTE_HEADINGS[match.group(1).lower()]
            current_lines = [match.group(2)] if match.group(2) else []
        elif current_key:
            current_lines.append(line)
    save_current()

    parsed = {
        key: "\n".join(values).strip()
        for key, values in sections.items()
        if values
    }
    if parsed.get("subjective") and not any(
        parsed.get(key)
        for key in (
            "chief_complaint",
            "hpi",
            "past_medical_history",
            "current_medications",
        )
    ):
        parsed["hpi"] = parsed["subjective"]
    return parsed


class EncounterContext(BaseModel):
    transcript: str = ""
    soap_note: str = ""
    patient_summary: str = ""
    key_highlights: list[str] = Field(default_factory=list)
    retrieved_guidelines: str = ""
    recommendations: Any = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    chief_complaint: str = ""
    hpi: str = ""
    past_medical_history: str = ""
    current_medications: str = ""
    objective: str = ""
    assessment: str = ""
    plan: str = ""

    def _resolved_sections(self) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for source in (self.soap_note, self.patient_summary):
            for key, value in _parse_note_sections(source).items():
                resolved.setdefault(key, value)
        if self.patient_summary.strip() and not _parse_note_sections(self.patient_summary):
            resolved.setdefault("hpi", self.patient_summary.strip())
        if self.soap_note.strip() and not _parse_note_sections(self.soap_note):
            resolved.setdefault("hpi", self.soap_note.strip())
        for key in (
            "chief_complaint",
            "hpi",
            "past_medical_history",
            "current_medications",
            "objective",
            "assessment",
            "plan",
        ):
            value = getattr(self, key, "")
            if value and value.strip():
                resolved[key] = value.strip()
        return resolved

    def as_patient_summary(self) -> str:
        sections = self._resolved_sections()
        sections = (
            ("Chief Complaint", sections.get("chief_complaint", "")),
            ("History of Present Illness", sections.get("hpi", "")),
            ("Past Medical History", sections.get("past_medical_history", "")),
            ("Current Medications", sections.get("current_medications", "")),
            ("Objective", sections.get("objective", "")),
            ("Assessment", sections.get("assessment", "")),
            ("Plan", sections.get("plan", "")),
        )
        return "\n\n".join(
            f"{heading}:\n{value.strip()}"
            for heading, value in sections
            if value and value.strip()
        )

    def as_soap_note(self) -> str:
        sections = self._resolved_sections()

        subjective = "\n\n".join(
            part
            for part in (
                sections.get("chief_complaint", ""),
                sections.get("hpi", ""),
                sections.get("past_medical_history", ""),
                sections.get("current_medications", ""),
            )
            if part and part.strip()
        ) or "Not documented."
        objective = sections.get("objective") or "Not documented."
        assessment = sections.get("assessment") or "Not documented."
        plan = sections.get("plan") or "Not documented."

        return (
            f"Subjective:\n{subjective}\n\n"
            f"Objective:\n{objective}\n\n"
            f"Assessment:\n{assessment}\n\n"
            f"Plan:\n{plan}"
        )


class AgentMetadata(BaseModel):
    agents_invoked: list[str] = Field(default_factory=list)
