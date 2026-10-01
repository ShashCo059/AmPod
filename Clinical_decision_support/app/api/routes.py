from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.api.schemas import (
    EncounterRequest,
    EncounterContextRequest,
    EncounterResponse,
    CodingRequest,
    CodingResponse,
    GenerateBillRequest,
    KnowledgeBaseRequest,
    KnowledgeBaseResponse,
    PatientCreateRequest,
    PatientRecordCreateRequest,
    PatientRecordsRequest,
    PatientUpdateRequest,
    PatientAnalysisRequest,
    PatientAnalysisResponse,
)
from app.agent.encounter_agent import EncounterAgent
from app.services.clinical_service import ClinicalAnalysisService
from app.services.billing_service import generate_patient_bill, save_generated_bill
from app.services.ehr_data_service import (
    create_patient,
    create_patient_record,
    delete_patient,
    get_patient_chart,
    load_ehr_data,
    list_patients,
    replace_patient_records,
    update_patient,
)
from cpt_coder import get_cpt_candidate_sets, get_cpt_codes, infer_documented_procedures
from icd10_coder import get_icd10_codes


router = APIRouter()


def get_encounter_agent() -> EncounterAgent:
    return EncounterAgent()


def get_transcription_service() -> TranscriptionService:
    from app.services.transcription_service import TranscriptionService

    return TranscriptionService()


def retrieve_documents(query: str, top_k: int) -> str:
    from app.rag.retriever import retrieve_documents as retrieve

    return retrieve(query, top_k)


@router.get("/", tags=["system"])
def root():
    return {"name": "Clinical Decision Support API", "docs": "/docs", "health": "/health"}


@router.get("/health", tags=["system"])
def health_check():
    return {"status": "ok"}


@router.get("/patients", tags=["patients"])
def get_patients():
    try:
        return {"patients": list_patients()}
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Patient data could not be loaded: {error}") from error


@router.get("/patients/export", tags=["patients"])
def export_patient_data():
    try:
        return load_ehr_data()
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Patient data could not be loaded: {error}") from error


@router.get("/patients/{patient_id}", tags=["patients"])
def get_patient(patient_id: str):
    try:
        return get_patient_chart(patient_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Patient chart could not be loaded: {error}") from error


@router.post("/patients", tags=["patients"])
def add_patient(request: PatientCreateRequest):
    try:
        return create_patient(request.values)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail=f"Patient could not be saved: {error}") from error


@router.put("/patients/{patient_id}", tags=["patients"])
def edit_patient(patient_id: str, request: PatientUpdateRequest):
    try:
        return update_patient(patient_id, request.values)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Patient could not be saved: {error}") from error


@router.put("/patients/{patient_id}/records/{sheet_name}", tags=["patients"])
def edit_patient_records(patient_id: str, sheet_name: str, request: PatientRecordsRequest):
    try:
        return {"records": replace_patient_records(patient_id, sheet_name, request.records)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Patient records could not be saved: {error}") from error


@router.post("/patients/{patient_id}/records/{sheet_name}", tags=["patients"])
def add_patient_record(patient_id: str, sheet_name: str, request: PatientRecordCreateRequest):
    try:
        return create_patient_record(patient_id, sheet_name, request.values)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Patient record could not be saved: {error}") from error


@router.delete("/patients/{patient_id}", tags=["patients"])
def remove_patient(patient_id: str):
    try:
        delete_patient(patient_id)
        return {"deleted": True, "patient_id": patient_id}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"Patient could not be deleted: {error}") from error


@router.post("/patients/{patient_id}/bills/ipd", tags=["billing"])
def generate_patient_ipd_bill(patient_id: str, request: GenerateBillRequest):
    try:
        bill = generate_patient_bill(
            patient_id,
            icd10_codes=request.icd10_codes,
            cpt_hcpcs_codes=request.cpt_hcpcs_codes,
            encounter_id=request.encounter_id,
            encounter_type=request.encounter_type,
        )
        return save_generated_bill(bill)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Patient not found.") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, ImportError) as error:
        raise HTTPException(status_code=503, detail=f"IPD bill source is unavailable: {error}") from error


@router.post("/analyze", response_model=PatientAnalysisResponse, tags=["clinical"])
def analyze_patient(request: PatientAnalysisRequest):
    try:
        result, raw_response = ClinicalAnalysisService().analyze(request.patient_summary)
    except ValueError as error:
        raise HTTPException(status_code=502, detail="The model response was not valid JSON.") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Clinical analysis failed: {error}") from error

    response_fields = {
        "possible_diagnosis",
        "risk_assessment",
        "recommended_tests",
        "treatment_suggestions",
        "follow_up_plan",
    }
    try:
        return PatientAnalysisResponse(
            raw_response=raw_response,
            **{key: result[key] for key in response_fields if key in result},
        )
    except ValidationError as error:
        raise HTTPException(status_code=502, detail="The model response had an invalid structure.") from error


@router.post("/encounters/analyze", response_model=EncounterResponse, tags=["encounters"])
def analyze_encounter(request: EncounterRequest):
    try:
        agent = get_encounter_agent()
        if request.include_cds and hasattr(agent, "process"):
            context = agent.process(request.transcript.strip())
        else:
            context = agent.prepare(request.transcript.strip())
            if request.include_cds:
                context = agent.add_clinical_decision_support(context)
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Ambient scribe response was invalid: {error}") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConnectionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Encounter analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/scribe", response_model=EncounterResponse, tags=["encounters"])
def scribe_encounter(request: EncounterRequest):
    try:
        context = get_encounter_agent().prepare(request.transcript.strip())
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Ambient scribe response was invalid: {error}") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Scribe analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/cds", response_model=EncounterResponse, tags=["encounters"])
def complete_encounter_with_cds(request: EncounterContextRequest):
    try:
        context = get_encounter_agent().add_clinical_decision_support(request)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ConnectionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"CDS analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/encounters/analyze-audio", response_model=EncounterResponse, tags=["encounters"])
async def analyze_audio(request: Request):
    try:
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise ValueError("An audio file is required.")
        transcript = get_transcription_service().transcribe(await file.read())
        context = get_encounter_agent().process(transcript.strip())
    except ValueError as error:
        raise HTTPException(status_code=502, detail=f"Encounter processing failed: {error}") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Audio encounter analysis failed: {error}") from error

    return EncounterResponse(**context.model_dump())


@router.post("/retrieve", response_model=KnowledgeBaseResponse, tags=["knowledge base"])
def retrieve_knowledge(request: KnowledgeBaseRequest):
    try:
        context = retrieve_documents(request.query.strip(), top_k=request.top_k)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"Knowledge-base retrieval failed: {error}") from error

    return KnowledgeBaseResponse(query=request.query, top_k=request.top_k, context=context)


@router.post("/codes", response_model=CodingResponse, tags=["coding"])
def match_codes(request: CodingRequest):
    """Return diagnosis codes and CPT/HCPCS codes for explicitly supplied items."""
    try:
        icd10 = get_icd10_codes(request.conditions, threshold=80)
        procedures = list(request.procedures) + infer_documented_procedures(request.documentation)
        documentation = request.documentation.lower()
        if any(term in documentation for term in ("a1c", "hba1c", "hemoglobin a1c")):
            procedures.append("A1C Test")
        if (
            ("chest x-ray" in documentation or "chest x ray" in documentation)
            and any(term in documentation for term in ("1-2 view", "1 to 2 view", "two view", "to view", "2 view"))
        ):
            procedures = [
                procedure for procedure in procedures
                if "chest x-ray" not in procedure.lower() and "chest x ray" not in procedure.lower()
            ]
            procedures.append("Chest x ray 2 views")
        procedures = list(dict.fromkeys(procedures))
        if request.documentation:
            details = get_cpt_candidate_sets(
                procedures,
                request.documentation,
                threshold=80,
            )
            cpt = [
                {
                    "Extracted Procedure": item["term"],
                    "Matched Procedure/Service": item["selected_description"],
                    "CPT/HCPCS Code": item["selected_code"],
                }
                for item in details
                if item["status"] == "suggested"
            ]
        else:
            cpt = get_cpt_codes(procedures, threshold=80)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Code matching failed: {error}") from error

    return CodingResponse(icd10=icd10, cpt=cpt)