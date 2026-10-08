from app.llm_client import call_llm
from app.agent.context import EncounterContext


class ClinicalDecisionSupportAgent:
    name = "clinical_decision_support"

    def analyze(self, patient_summary):
        encounter = EncounterContext(patient_summary=patient_summary)
        self.analyze_context(encounter)
        return encounter.recommendations

    def analyze_context(self, encounter: EncounterContext) -> EncounterContext:
        from app.rag.retriever import retrieve_documents

        structured_context = (
            encounter.as_patient_summary().strip()
            or encounter.patient_summary.strip()
        )
        transcript = encounter.transcript.strip()
        if structured_context and transcript:
            clinical_context = (
                f"Structured clinical documentation:\n{structured_context}\n\n"
                f"Original consultation transcript:\n{transcript}"
            )
        else:
            clinical_context = structured_context or transcript
        if not clinical_context:
            raise ValueError(
                "CDS requires a patient summary, structured encounter details, or transcript."
            )

        guideline_context = retrieve_documents(clinical_context, top_k=5)
        encounter.retrieved_guidelines = guideline_context
        prompt = (
            "Prepare cautious, clinician-facing educational decision support. Use only facts "
            "documented in the encounter and the supplied guideline passages. Do not invent "
            "symptoms, examination findings, diagnoses, medication details, or test results. "
            "Separate documented facts from interpretation, explain the reasoning, identify "
            "important missing information, and state uncertainty. Do not present a treatment "
            "or test as a guideline recommendation unless a supplied passage supports it. "
            "If no relevant passage is supplied, say so explicitly and do not imply that "
            "Harrison's supports a recommendation. Return these sections: Clinical context, "
            "Reasoning, Evidence and citations, Considerations for clinician review, Missing "
            "information, and Safety note. Cite supporting passages using their exact "
            "'Source:' citation labels. State that this is educational decision support, "
            "not a diagnosis or a substitute for clinician judgment.\n\n"
            f"Encounter information:\n{clinical_context}\n\n"
            f"Retrieved guideline context:\n{guideline_context}\n"
        )
        citation_labels = [
            line.strip()
            for line in guideline_context.splitlines()
            if line.strip().startswith("Source:")
        ]
        recommendation = ""
        failure_reason = "The CDS model returned an empty response."
        for _ in range(2):
            recommendation = str(
                call_llm(
                    prompt=prompt,
                    system_prompt=(
                        "You provide careful, evidence-grounded clinical decision support. "
                        "Do not guess, overstate certainty, or claim that a source says more "
                        "than the supplied text supports."
                    ),
                )
                or ""
            ).strip()
            if not recommendation:
                failure_reason = "The CDS model returned an empty response."
                continue
            if citation_labels and not any(
                citation in recommendation for citation in citation_labels
            ):
                failure_reason = (
                    "The CDS model did not cite any of the retrieved guideline passages."
                )
                continue
            break
        else:
            raise RuntimeError(f"{failure_reason} No recommendation was generated after retrying.")

        encounter.recommendations = recommendation
        return encounter

    def generate(self, patient_summary):
        return self.analyze(patient_summary)
