from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent.ambient_scribe_agent import AmbientScribeAgent
from app.agent.context import EncounterContext

if TYPE_CHECKING:
    from app.agent.cds_agent import ClinicalDecisionSupportAgent


class EncounterAgent:
    """Orchestrate encounter-level agents without coupling agent implementations."""

    def __init__(
        self,
        scribe: AmbientScribeAgent | None = None,
        cds: ClinicalDecisionSupportAgent | None = None,
    ):
        if cds is None:
            from app.agent.cds_agent import ClinicalDecisionSupportAgent

            cds = ClinicalDecisionSupportAgent()
        self.scribe = scribe or AmbientScribeAgent()
        self.cds = cds

    def process(self, transcript: str) -> EncounterContext:
        context = self.prepare(transcript)
        context = self.cds.analyze_context(context)
        context.metadata["agents_invoked"] = [self.scribe.name, self.cds.name]
        return context

    def prepare(self, transcript: str) -> EncounterContext:
        context = self.scribe.transcribe(transcript)
        if not context.patient_summary:
            context.patient_summary = context.as_patient_summary()
        context.soap_note = context.as_soap_note()
        context.metadata["agents_invoked"] = [self.scribe.name]
        return context

    def add_clinical_decision_support(self, context: EncounterContext) -> EncounterContext:
        context = self.cds.analyze_context(context)
        context.metadata["agents_invoked"] = [self.scribe.name, self.cds.name]
        return context