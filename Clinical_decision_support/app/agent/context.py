from typing import Any

from pydantic import BaseModel, Field


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

    def as_patient_summary(self) -> str:
        return (
            f"Chief Complaint:\n{self.chief_complaint}\n\n"
            f"History of Present Illness:\n{self.hpi}\n\n"
            f"Past Medical History:\n{self.past_medical_history}\n\n"
            f"Current Medications:\n{self.current_medications}\n\n"
            f"Assessment:\n{self.assessment}\n\n"
            f"Plan:\n{self.plan}"
        )

    def as_soap_note(self) -> str:
        sections: dict[str, str] = {
            "chief complaint": self.chief_complaint,
            "history of present illness": self.hpi,
            "past medical history": self.past_medical_history,
            "current medications": self.current_medications,
            "objective": self.objective,
            "assessment": self.assessment,
            "plan": self.plan,
        }

        if self.patient_summary and not any(part.strip() for part in sections.values()):
            summary_lines = self.patient_summary.splitlines()
            parsed: dict[str, str] = {}
            current_key: str | None = None
            buffer: list[str] = []
            for line in summary_lines:
                stripped = line.strip()
                if not stripped:
                    if current_key and buffer:
                        parsed[current_key] = "\n".join(buffer).strip()
                        buffer = []
                    continue
                normalized = stripped.lower()
                if normalized.startswith("chief complaint:"):
                    current_key = "chief complaint"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if normalized.startswith("history of present illness:"):
                    current_key = "history of present illness"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if normalized.startswith("past medical history:"):
                    current_key = "past medical history"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if normalized.startswith("current medications:"):
                    current_key = "current medications"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if normalized.startswith("assessment:"):
                    current_key = "assessment"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if normalized.startswith("plan:"):
                    current_key = "plan"
                    buffer = [stripped.split(":", 1)[1].strip()] if ":" in stripped else []
                    continue
                if current_key:
                    buffer.append(stripped)
            if current_key and buffer:
                parsed[current_key] = "\n".join(buffer).strip()

            sections = {
                "chief complaint": parsed.get("chief complaint", self.chief_complaint),
                "history of present illness": parsed.get("history of present illness", self.hpi),
                "past medical history": parsed.get("past medical history", self.past_medical_history),
                "current medications": parsed.get("current medications", self.current_medications),
                "objective": parsed.get("objective", self.objective),
                "assessment": parsed.get("assessment", self.assessment),
                "plan": parsed.get("plan", self.plan),
            }

        subjective = "\n\n".join(
            part
            for part in (
                sections.get("chief complaint", ""),
                sections.get("history of present illness", ""),
                sections.get("past medical history", ""),
                sections.get("current medications", ""),
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
