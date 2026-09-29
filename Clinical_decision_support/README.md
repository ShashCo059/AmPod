# Clinical Decision Support and Ambient Scribe

A local clinical documentation and decision-support prototype. The application records or accepts a clinical conversation, creates a structured encounter note with an LLM, retrieves relevant guidance from a local PDF knowledge base, and generates clinical decision-support recommendations.

> This is an educational prototype. It is not a medical device and must not replace a licensed clinician, emergency services, local protocols, or professional clinical judgment.

## What The Application Does

1. A clinician opens the Streamlit interface.
2. The clinician enters patient and doctor details and records audio.
3. Streamlit transcribes the audio locally with SpeechRecognition and Google speech recognition.
4. The transcript is sent to the FastAPI `/encounters/scribe` endpoint.
5. The ambient scribe agent calls the configured Coforge LLM and converts the transcript into structured encounter fields.
6. The clinician can request CDS recommendations.
7. The CDS agent retrieves relevant chunks from the local knowledge base using TF-IDF similarity and ChromaDB storage.
8. The CDS agent sends the patient summary and retrieved guidance to the LLM.
9. The UI displays the transcript, summary, recommendations, and detected insights.
10. The clinician can save a draft or finalize the note in `clinical_records.json`.

## Architecture

```text
Streamlit UI
  frontend/streamlit_app.py
        |
        v
frontend/api_client.py
        |
        | HTTP JSON requests to http://127.0.0.1:8000
        v
FastAPI application
  app/main.py + app/api/routes.py
        |
        +--> AmbientScribeAgent --> SoapService --> Coforge LLM API
        |
        +--> ClinicalDecisionSupportAgent
                  |
                  +--> TF-IDF retrieval + ChromaDB
                  +--> Coforge LLM API
        |
        +--> clinical_records.json
```

The application has two processing stages:

- **Scribe stage:** transcript to structured clinical encounter data.
- **CDS stage:** encounter context plus local retrieved guidance to clinical recommendations.

The Streamlit client uses the separate `/encounters/scribe` and `/encounters/cds` calls so the note can be generated before CDS is requested.

## Requirements

- Windows, macOS, or Linux
- Python 3.10 or newer recommended
- A working microphone for browser recording
- Internet access for:
  - Coforge LLM Router API
  - Google speech recognition used by SpeechRecognition
- A valid Coforge API key and endpoint
- Project dependencies from `requirements.txt`

## Installation

Run these commands from the `Clinical_decision_support` directory.

### 1. Create a virtual environment

If the repository virtual environment already exists at `D:\Agent pod\venv`, it can be reused. Otherwise create one from the parent directory:

```powershell
cd "D:\Agent pod"
python -m venv venv
```

Activate it:

```powershell
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use the interpreter directly instead of changing execution policy:

```powershell
& "D:\Agent pod\venv\Scripts\python.exe" --version
```

### 2. Install dependencies

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m pip install -r requirements.txt
```

Important: use the same `venv\Scripts\python.exe` for installation and for starting FastAPI. The machine may have multiple Windows Store Python launchers. Installing with one interpreter and running with another causes errors such as `No module named 'chromadb'`.

### 3. Configure the LLM API

Create or edit `.env` in this directory:

```dotenv
COFORGE_API_KEY=replace-with-your-key
COFORGE_API_URL=https://your-llm-router.example/v2/chat/completions
COFORGE_MODEL=router
```

Never commit a real API key. The application loads `.env` through `app/core/config.py`.

## Running The Application

Use two terminals. Start both from `D:\Agent pod\Clinical_decision_support`.

### Terminal 1: FastAPI backend

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m app
```

The API runs at:

- Root: http://127.0.0.1:8000/
- Health: http://127.0.0.1:8000/health
- Swagger UI: http://127.0.0.1:8000/docs

Expected health response:

```json
{"status":"ok"}
```

`app/__main__.py` intentionally starts Uvicorn without reload. This keeps child processes from switching to a different Python interpreter on Windows.

### Terminal 2: Streamlit frontend

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m streamlit run frontend/streamlit_app.py
```

Open the URL printed by Streamlit, normally http://localhost:8501.

### Quick backend check

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/health `
  -UseBasicParsing
```

If the UI says it cannot connect, check that port 8000 is listening and that the API terminal is running from the project directory with the venv interpreter.

## API Endpoints

All endpoints are defined in `app/api/routes.py`.

| Method | Path | Purpose | Request body |
| --- | --- | --- | --- |
| GET | `/` | API metadata and links | None |
| GET | `/health` | Health check | None |
| POST | `/analyze` | Legacy patient-summary analysis returning parsed JSON CDS fields | `{"patient_summary":"..."}` |
| POST | `/encounters/scribe` | Generate a structured encounter note without CDS | `{"transcript":"...", "include_cds":false}` |
| POST | `/encounters/analyze` | Run scribe and CDS in one backend operation | `{"transcript":"...", "include_cds":true}` |
| POST | `/encounters/cds` | Add CDS to an existing encounter context | Encounter context JSON |
| POST | `/encounters/analyze-audio` | Backend-side audio transcription and analysis | Multipart form field `file` |
| POST | `/retrieve` | Retrieve local knowledge-base context | `{"query":"...", "top_k":5}` |

Example scribe request:

```powershell
$body = @{ transcript = "Patient reports a cough for two days."; include_cds = $false } | ConvertTo-Json
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/encounters/scribe `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

Example combined encounter request:

```powershell
$body = @{ transcript = "Patient reports a cough for two days."; include_cds = $true } | ConvertTo-Json
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/encounters/analyze `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

## Knowledge Base

The source knowledge base is `knowledge_base/knowledge_base_harrison.docx`.

`app/scripts/build_kb.py` performs these steps:

1. Extract Word paragraphs and tables while preserving heading context.
2. Split each document block into overlapping clinical text chunks with source metadata.
3. Generate normalized Sentence Transformer embeddings in batches.
4. Replace the current Chroma collection using batched upserts.
5. Retrieve the nearest candidate chunks directly from Chroma.
6. Deduplicate candidates and bound the cited context sent to the LLM.

Run it after changing the PDF or when generated artifacts are missing:

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m app.scripts.build_kb
```

The retriever uses Chroma's cosine vector search and adds source filename, document block, and heading citations to the CDS context. Word documents do not reliably expose printed page numbers through `python-docx`, so citations use block and heading metadata. `chroma_db` is generated runtime data and should be treated as an application artifact. The first build downloads the configured Sentence Transformer model.

For a large PDF, these optional `.env` settings control resource usage:

```dotenv
EMBEDDING_BACKEND=auto
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_BATCH_SIZE=64
RETRIEVAL_CANDIDATES=20
RETRIEVAL_RESULTS=5
MAX_CONTEXT_CHARS=12000
```

`EMBEDDING_BACKEND=auto` uses Sentence Transformers when the model is available locally or can be downloaded. If model download fails, it automatically builds a sparse TF-IDF index instead. Set `EMBEDDING_BACKEND=tfidf` to force fully offline indexing, or `EMBEDDING_BACKEND=sentence_transformer` to fail instead of falling back.

If the Word file contains images of scanned pages, OCR them before indexing; `python-docx` extracts document text but does not OCR embedded images.

## File Guide

### Application entry points

- `app/__main__.py`: command-line entry point for `python -m app`; starts Uvicorn on port 8000.
- `app/main.py`: creates the FastAPI application and registers the API router.
- `frontend/streamlit_app.py`: main Streamlit UI, page navigation, recording workflow, API calls, dashboard, review screen, and local record persistence.
- `frontend/api_client.py`: small HTTP client used by Streamlit to call the FastAPI backend.

### API layer

- `app/api/routes.py`: HTTP route handlers, error translation, lazy loading of optional RAG and audio dependencies.
- `app/api/schemas.py`: Pydantic request and response models for patient analysis, encounters, and retrieval.
- `app/api/__init__.py`: API package marker.

### Agents and encounter context

- `app/agent/context.py`: shared `EncounterContext` Pydantic model containing transcript, SOAP note, patient summary, highlights, recommendations, and clinical fields.
- `app/agent/ambient_scribe_agent.py`: ambient scribe agent that turns transcript text into structured encounter context.
- `app/agent/cds_agent.py`: retrieves guidance and asks the LLM for CDS recommendations.
- `app/agent/encounter_agent.py`: orchestrates scribe-only, CDS-only, and combined encounter processing.
- `app/agent/__init__.py`: agent package marker.
- `app/agents/base_agent.py`: base-agent package implementation retained for the broader agent structure.
- `app/agents/__init__.py`: agents package marker.

### Services and external integrations

- `app/llm_client.py`: sends prompts to the Coforge LLM Router API, validates configuration, retries transient HTTP failures, and returns the model message.
- `app/services/soap_service.py`: prompts the LLM for a JSON SOAP note and parses the response.
- `app/services/clinical_service.py`: legacy clinical-analysis service that parses an LLM JSON response.
- `app/services/transcription_service.py`: backend-side audio transcription using SpeechRecognition.
- `app/services/__init__.py`: services package marker.
- `app/core/config.py`: loads `.env`, defines project paths, and exposes LLM configuration values.
- `app/core/__init__.py`: core package marker.

### Retrieval-augmented generation

- `app/rag/pdf_loader.py`: extracts text from one PDF or all PDFs in a folder.
- `app/rag/chunker.py`: splits source text into overlapping chunks with LangChain text splitters.
- `app/rag/embeddings.py`: creates the TF-IDF vectorizer and matrix.
- `app/rag/vector_store.py`: initializes the persistent ChromaDB collection and stores chunks with embeddings.
- `app/rag/retriever.py`: loads persisted TF-IDF artifacts, scores chunks, and returns relevant guideline text.
- `app/rag/__init__.py`: RAG package marker.
- `app/scripts/build_kb.py`: command-line knowledge-base builder.
- `app/scripts/__init__.py`: scripts package marker.

### Rules and domain models

- `app/rules/rule_engine.py`: deterministic checks for HbA1c, blood pressure, chest pain, retinal screening gaps, and medication allergies.
- `app/rules/__init__.py`: rules package marker.
- `app/models/encounter.py`: encounter model definitions used by the application structure.
- `app/models/__init__.py`: models package marker.

### Tests

- `app/tests/test_api.py`: health, retrieval, legacy analysis, validation, and encounter API tests.
- `app/tests/test_agents.py`: ambient scribe response parsing test.
- `app/tests/test_clinical_service.py`: clinical-service parsing and behavior tests.
- `app/tests/__init__.py`: test package marker.

### Data and runtime artifacts

- `clinical_records.json`: Streamlit's local record database for this application.
- `knowledge_base/diseases.pdf`: source clinical guidance document.
- `chroma_db/chroma.sqlite3`: persistent ChromaDB database.
- `chroma_db/tfidf_vectorizer.pkl`: serialized TF-IDF vectorizer.
- `chroma_db/tfidf_matrix.npz`: serialized sparse TF-IDF matrix.
- `.env`: local secrets and LLM configuration; do not commit real credentials.
- `requirements.txt`: Python dependencies.

## Important Code Flows

### Recording to note

`frontend/streamlit_app.py` receives `st.audio_input`, reads the audio bytes, and transcribes them locally. The resulting text is passed to `HealthcareApiClient.scribe_encounter`. The client calls `/encounters/scribe`, which creates an `EncounterAgent` and calls `prepare`. The ambient scribe agent produces an `EncounterContext`, which is returned to Streamlit and displayed.

### Note to CDS recommendation

When the user selects CDS and presses **Generate Note**, Streamlit passes the existing encounter context to `HealthcareApiClient.add_cds`. The client calls `/encounters/cds`. The backend invokes `EncounterAgent.add_clinical_decision_support`, which calls `ClinicalDecisionSupportAgent.analyze_context`. That method retrieves guidance, builds a prompt containing the patient summary and retrieved context, calls the Coforge LLM, and writes the recommendation into the encounter context.

### Saving a record

After the optional CDS request succeeds, Streamlit builds a record dictionary containing patient metadata, transcript, summary, retrieved guidelines, recommendations, and analysis version. It inserts the record at the beginning of `st.session_state.records`, writes `clinical_records.json`, and opens the review page.

### HCC / medical coding workflow

The coding workspace creates machine-suggested diagnosis and procedure codes from the clinical insights detected in an encounter transcript. In this repository, “HCC coding” currently means ICD-10 diagnosis matching and CPT/HCPCS procedure matching. It does not calculate CMS-HCC categories, RAF scores, payment models, or final billable coding decisions.

The flow is:

1. `get_detected_insights()` in `frontend/streamlit_app.py` scans the transcript for supported diagnoses, symptoms, risk factors, tests, and procedures. Examples include hypertension, diabetes, fever, CBC, A1C, chest X-ray, CT scan, MRI, and ultrasound.
2. On the **Codes** page, the clinician selects an encounter and chooses **Generate Code Suggestions**.
3. `get_code_suggestions()` removes duplicates and sends each insight to `classify_medical_item()` in `code_matcher.py`.
4. Diagnosis, symptom, disease, and injury terms are routed to ICD-10. Test, imaging, therapy, and other procedure/service terms are routed to CPT/HCPCS. The UI also has a procedure-term fallback for items that the classifier cannot categorize.
5. `get_medical_codes()` loads the local ICD-10 and CPT/HCPCS datasets, checks exact and phrase matches first, and then uses RapidFuzz `WRatio` fuzzy matching. The Streamlit workflow uses an 80% similarity threshold and returns one strongest match per item by default.
6. Common procedures and diagnoses have deterministic fallback mappings when a suitable dataset entry is unavailable, such as CBC (`85025`), A1C (`83036`), chest X-ray (`71045`/`71046`), hypertension (`I10`), and diabetes (`E11.9`).
7. Results are displayed separately in the **ICD-10 Diagnosis Codes** and **CPT/HCPCS Procedure Codes** tabs. Each result includes the extracted term, matched description, and code.

The matcher looks for these dataset files relative to `code_matcher.py`:

- `ICD10CM_2022_Codes.json` or another supported ICD-10 JSON filename
- `CPT_CODES.json`, `structured_cpt_hcpcs_2026.json`, or another supported CPT/HCPCS JSON filename

The coding feature is intentionally conservative: unsupported or ambiguous terms are not forced into a code family, and a fuzzy match is only returned when it meets the threshold. Code suggestions must be reviewed by a qualified coder or clinician before billing, claim submission, or adding them to the legal health record. The current prototype does not persist approved codes back into `clinical_records.json`.

## Testing

Run the test suite with the project interpreter:

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m pytest -q
```

Run only API tests:

```powershell
& "D:\Agent pod\venv\Scripts\python.exe" -m pytest app/tests/test_api.py -q
```

Tests should not require a real LLM call when mocks are used. End-to-end manual testing does require valid `.env` configuration and network access to the Coforge service.

## Troubleshooting

### `No module named 'chromadb'`

The backend is probably using global Python instead of the repository virtual environment. Stop old Uvicorn processes and run:

```powershell
cd "D:\Agent pod\Clinical_decision_support"
& "D:\Agent pod\venv\Scripts\python.exe" -m app
```

Confirm the dependency:

```powershell
& "D:\Agent pod\venv\Scripts\python.exe" -c "import chromadb; print(chromadb.__version__)"
```

### Cannot connect to port 8000

Check the health endpoint:

```powershell
Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing
```

If it fails, start the backend from the `Clinical_decision_support` directory. Running `python -m app` from `D:\Agent pod` cannot find this project's `app` package.

### Missing LLM configuration

Check that `.env` exists in `Clinical_decision_support` and contains non-empty `COFORGE_API_KEY` and `COFORGE_API_URL` values. Restart the backend after changing `.env`.

### Knowledge-base artifacts are missing or empty

Run:

```powershell
& "D:\Agent pod\venv\Scripts\python.exe" -m app.scripts.build_kb
```

Then restart the backend and retry CDS.

### CDS returns a model or network error

The backend and retrieval layer may be healthy while the external LLM is unavailable. Inspect the FastAPI terminal for the upstream status code, verify the API URL and key, and confirm the model name accepted by the configured router.

### Audio recording or transcription fails

Check browser microphone permissions, confirm the recording has a supported audio format, and verify that SpeechRecognition can reach its speech-recognition provider. The current Streamlit flow transcribes locally; the backend audio endpoint is a separate optional path.

## Notes For Future LLM-Assisted Development

When analyzing this repository, start with these files in order:

1. `README.md` for architecture and commands.
2. `frontend/streamlit_app.py` for the user workflow.
3. `frontend/api_client.py` for the HTTP contract used by the UI.
4. `app/api/routes.py` and `app/api/schemas.py` for backend boundaries.
5. `app/agent/encounter_agent.py` and `app/agent/context.py` for orchestration and data shape.
6. `app/agent/ambient_scribe_agent.py` and `app/agent/cds_agent.py` for model behavior.
7. `app/llm_client.py` and `app/core/config.py` for external configuration.
8. `app/rag/retriever.py`, `app/rag/vector_store.py`, and `app/scripts/build_kb.py` for knowledge retrieval.
9. `app/tests/` before changing behavior.

Preserve the distinction between:

- UI-local audio transcription and the backend audio endpoint.
- Scribe-only processing and CDS processing.
- Persisted TF-IDF artifacts and the ChromaDB document collection.
- Local JSON record storage and the external LLM service.

Avoid logging or exposing `COFORGE_API_KEY`, patient identifiers, raw recordings, or full clinical records in diagnostics.

## Project Status

The application is a local prototype with a Streamlit frontend, FastAPI backend, local RAG artifacts, deterministic clinical rules, and an external LLM integration. It does not currently provide authentication, multi-user storage, production audit controls, encryption, clinical validation, or deployment configuration.
