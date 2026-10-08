# NuuCare Clinical Scribe, EHR Demo, and Billing

This project is a local demonstration application for recording a synthetic
clinical encounter, drafting a SOAP note, requesting evidence-grounded clinical
decision support (CDS), reviewing diagnosis/procedure code suggestions, browsing
synthetic EHR records, and generating a draft hospital bill.

> **Educational demonstration only.** This is not a production EHR, medical
> device, insurance eligibility system, coding authority, or substitute for
> licensed clinical judgment, emergency services, or local protocols. Do not
> enter real patient information (PHI). The microphone workflow sends audio to
> Google's speech-recognition service; clinical text is sent to the configured
> Coforge LLM service.

## Contents

- [Application at a glance](#application-at-a-glance)
- [Current user flow](#current-user-flow)
- [Billing and payer behavior](#billing-and-payer-behavior)
- [Data and persistence](#data-and-persistence)
- [Setup](#setup)
- [Run the application](#run-the-application)
- [API reference](#api-reference)
- [Knowledge base and CDS evidence](#knowledge-base-and-cds-evidence)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Project map](#project-map)
- [Changes represented in the current implementation](#changes-represented-in-the-current-implementation)
- [Limitations and safety](#limitations-and-safety)

## Application at a glance

The application has one FastAPI backend and two Streamlit frontends:

| Component | Default address | Entry point | Intended use |
| --- | --- | --- | --- |
| FastAPI backend | `http://127.0.0.1:8001` | `python -m app` | Shared clinical, EHR, coding, and billing API |
| Original Streamlit UI | `http://127.0.0.1:8502` | `frontend/streamlit_app.py` | Original consultation and clinical workflow |
| Athena Streamlit UI | `http://127.0.0.1:8503` | `frontend/streamlit1_app.py` | Current EHR and Athena billing workflow |

The current workflow is on port **8503**. It uses the shared backend and the
Athena charge rows. The 8502 frontend remains available for the original
consultation workflow.

```text
Browser
  ├── Streamlit UI (8502 or 8503)
  │     ├── local clinical_records.json for consultation workspaces
  │     └── HealthcareApiClient
  └── FastAPI backend (8001)
        ├── EHR JSON read/write
        ├── ICD-10 and CPT/HCPCS suggestions
        ├── synthetic charge-sheet billing
        └── encounter agents
              ├── ambient scribe → Coforge LLM
              └── CDS → local knowledge retrieval → Coforge LLM
```

## Current user flow

### 1. Start a consultation

1. Open **New Consultation** and select a synthetic patient from the demo
   directory, or enter the required details for a new patient.
2. Record audio in the browser. The Streamlit frontend sends the recording to
   Google's speech-recognition service and receives a transcript.
3. The frontend sends the transcript to `POST /encounters/analyze`.
4. The backend runs the ambient scribe, constructs SOAP fields and a patient
   summary, retrieves local guidance for CDS, and requests a recommendation.
5. The frontend lets the user review the transcript, note, highlights, and any
   CDS result before saving the consultation.
6. When saving, the frontend creates/links the EHR patient and encounter note
   through the patient APIs, saves the consultation workspace record to
   `clinical_records.json`, and opens the review flow.

Audio transcription requires network access to Google's service. A transcript
can also be supplied directly through the API; microphone recording is not
required for API clients.

### 2. SOAP note generation and recovery behavior

The scribe asks the configured LLM to return structured JSON. The backend
parses the response into an `EncounterContext` and formats the SOAP note from
its clinical fields.

- The combined encounter flow retries scribe generation once after recognized
  provider, parsing, or response-validation failures.
- If those attempts fail, it retains the original transcript in a clearly
  labeled transcript-only note shell. The shell does not invent missing
  examination findings, diagnoses, medications, or plans. Generation metadata
  marks this as a fallback and the UI warns the user to review it.
- **Generate SOAP** on the Review screen reruns the scribe against the stored
  transcript. It does not replace the transcript with a new recording.
- A transcript-only fallback is not a completed clinical note and must be
  reviewed and completed by a qualified clinician before approval.

Retries improve recovery from transient or malformed responses; they cannot
ensure a remote model, network, or provider is always available.

### 3. CDS generation and evidence

The CDS agent:

1. Builds a retrieval query from the structured encounter and original
   transcript.
2. Retrieves relevant text from the local knowledge-base index.
3. Prompts the LLM to separate documented facts from interpretation, state
   missing information and uncertainty, and cite retrieved source labels.
4. Rejects empty output and, when source passages were retrieved, output that
   cites none of those passages.
5. Retries an empty or uncited model answer once. If the combined encounter
   encounters a handled CDS/provider/retrieval failure, it returns the
   SOAP/transcript result with CDS marked unavailable; it does not substitute
   generic medical advice.

The Review screen's **Regenerate CDS from Transcript** action retries CDS using
the saved encounter context. A failed retry is shown as a failure and does not
overwrite an existing recommendation with generic content. An unavailable
recommendation is not evidence of a clinically normal result.

The combined request is `POST /encounters/analyze`. Separate API operations are
also available: `POST /encounters/scribe` for a scribe-only request and
`POST /encounters/cds` to add CDS to a previously created context. See
[API reference](#api-reference).

### 4. Review and approve

The Review screen displays the transcript, editable SOAP summary, generated
CDS, and encounter status. SOAP can be regenerated from the transcript; CDS can
be regenerated separately. Review generated text and evidence before approval.
The application stores consultation records locally and stores the linked
clinical note in the EHR demo JSON.

Reviewers can record an **Accepted** or **Overridden** CDS decision; an
override requires a rationale. Before/after review events are stored in the
consultation workspace. These demo events use a generic actor label and are
not authenticated or tamper-proof audit evidence.

### 5. Generate code suggestions

The **Codes** page derives clinical insights from the transcript and selected
manual insights, then requests ICD-10-CM and CPT/HCPCS candidates. The matcher
uses the project's coding data and deterministic mappings for supported common
terms. Results are suggestions, not final coding decisions.

Review codes before applying them to the billing workflow. Bill generation
requires a CPT/HCPCS code and confirmation that the displayed services were
performed and documented for the selected encounter. Do not bill a suggested
service merely because it appears in a transcript or code list.

### 6. Browse the EHR demo

In the alternate UI, open **EHR Demo Data**:

- The patient selector shows patient ID, name, and assigned payer when present.
- The selected patient summary shows the primary payer and plan.
- **Patient Master** includes read-only Primary Payer and Plan columns. Payer
  values are derived from the active row in the `Insurance` sheet; they are not
  demographic fields stored on the Patient Master record.
- Other patient-linked sheets (encounters, notes, insurance, orders, and
  generated billing data) can be inspected and edited according to the UI's
  available controls.
- Adding a patient requires choosing an initial synthetic payer. The backend
  creates the patient and primary coverage together.

Saving a Patient Master edit does not directly change coverage. Coverage is
stored in the EHR JSON's `Insurance` sheet.

## Billing and payer behavior

### Eligible patients

In the alternate UI's **Codes** billing section, the patient selector excludes
the original 50 seed charts (`P100001` through `P100050`). Billing there is
available for John F. Kennedy (`P100051`), Donald Trump (`P100052`), and
patients added after them. This restriction applies to that bill-generation
selector; it does not delete or hide the original 50 charts from the EHR
directory.

The consultation must be linked to the intended EHR patient and encounter.
Review and repair patient links before billing; the page intentionally avoids
silently assuming an ambiguous patient match.

### Select payer and build the bill

1. On **Codes**, select the consultation and confirm the linked patient and
   encounter.
2. Review the CPT/HCPCS codes and confirm that the services were performed and
   documented for that encounter. The patient chart review panel shows the
   selected encounter and relevant problems, allergies, medications, vitals,
   labs, and orders. Coding evidence exposes the source transcript and matched
   candidates.
   **Physician documentation query** supports a manual neutral query draft,
   documented clinical indicators, optional response choices, and a saved
   provider response. Queries remain local to the consultation; they are not
   sent externally or written into the EHR.
3. The bill payer is fixed to **Athena Health Insurance**. The payer is shown
   for both seed and newly added patients; it is not selectable.
4. Select **Generate Bill**. The backend reads matching Athena `OPD Charges`
   or `IPD Charges` records from the root-level
   `Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json`, based on the encounter
   type. It prices encounter-linked procedures and the reviewed code list from
   that source only, and creates a new bill with a new bill ID.
5. The generated bill is saved as a draft with its bill lines and draft claim
   rows. It displays gross charges, Athena expected allowed amount, estimated
   patient responsibility, and the hospital account.
6. **Validate Bill** checks that the bill matches the selected patient,
   encounter, and payer, that it has priced lines, and that line gross charges
   add up to the bill total. Validation does **not** submit a claim and does
   **not** assign or change payer coverage.
7. On successful bill saving, Athena is written to the patient's active
   synthetic primary coverage in the `Insurance` sheet. Existing historical
   bills keep their original payer as an audit record.

> **Important:** Payer coverage assignment happens when the bill is **generated
> and saved**, not when it is validated. For a bill created before payer
> persistence was added, generate/save a bill with the intended payer to
> synchronize the patient coverage. A validation click alone cannot do that.

### What billing does—and does not—mean

- Gross charge rates and Athena allowed/patient-share amounts are synthetic
  estimates from the hospital charge sheet.
- If any code lacks a rate in the selected encounter type's Athena-only JSON
  records, bill generation fails without saving a partial bill. It is never
  priced using an unrelated CPT code or another payer's data.
- A generated bill is a local draft. Draft claim rows are not submitted to an
  insurer.
- No payer eligibility, member identity, benefits, authorization, adjudication,
  payment, or real claim submission is performed.
- A complete bill already present for the same patient, encounter, payer, and
  CPT set is rejected with HTTP 409. A process-local write lock protects the
  JSON check-and-save from concurrent requests in this backend process.
- The hospital account is tied to the encounter and reused when that
  encounter is billed.
- Historical generated bills retain the payer and rates used at creation.

## Data and persistence

The backend resolves demo data paths from the repository, not the shell's
current directory.

| Data | Location | Purpose |
| --- | --- | --- |
| Current EHR workbook-as-JSON | `../Epic_Inspired_USA_50_Patient_Demo_25_Athena_25_Care_Gap.json` relative to this README | Patient, Athena active insurance, encounters, notes, orders, bills, lines, claims, and append-only demo edit log |
| Original demo EHR JSON | `../Epic_Inspired_USA_50_Patient_Demo.json` | Original demo dataset used by older workflows/artifacts |
| Athena rate source | `../Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json` | Athena-only IPD and OPD rates and synthetic allocation estimates |
| Consultation workspace | `clinical_records.json` in this project directory | Locally persisted Streamlit consultation records and status |
| Knowledge source | `knowledge_base/knowledge_base_harrison.docx` | Source document indexed for CDS retrieval |
| Retrieval artifacts | `chroma_db/` | Local vector/TF-IDF index and source chunks |

The EHR JSON is the source for patient and coverage data. Generated bills,
bill lines, claims, and audit events are written to their EHR sheets. File
writes use a temporary file and atomic replacement to reduce partial-write
risk. The duplicate-bill check is serialized within one backend process; this
local JSON demonstration store does not provide cross-process transactions,
authenticated actors, tamper-proof audit evidence, or database-grade
backup/restore.

Before manually editing the JSON, stop the backend and keep a backup. Do not
edit the live file while the application is running.

## Setup

Run the commands from the `Clinical_decision_support` directory.

### 1. Create and activate the project environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use the environment's interpreter directly:

```powershell
& ".\.venv\Scripts\python.exe" --version
```

### 2. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

Use this same virtual environment for starting the backend and Streamlit.
Installing a package into a different Python environment can cause import
errors at startup.

### 3. Configure the LLM

Create `Clinical_decision_support/.env` (or configure the parent `.env`) with
the values required by your Coforge LLM Router account:

```dotenv
COFORGE_API_KEY=replace-with-your-key
COFORGE_API_URL=https://your-llm-router.example/v2/chat/completions
COFORGE_MODEL=your-enabled-model

# Optional: CPT-specific route; falls back to the general settings if omitted.
COFORGE_CPT_API_KEY=
COFORGE_CPT_API_URL=
COFORGE_CPT_MODEL=
```

Never commit a real API key. The backend must be restarted after changing
`.env`. A health response only confirms the API process is responding; it does
not test the LLM key, knowledge base, or billing workbook.

## Run the application

Open two terminals in `Clinical_decision_support`. In both, activate `.venv`
or invoke its Python executable directly.

### Terminal 1 — backend

```powershell
Remove-Item Env:PORT -ErrorAction SilentlyContinue
Remove-Item Env:HOST -ErrorAction SilentlyContinue
python -m app
```

Expected endpoints:

- API root: `http://127.0.0.1:8001/`
- Health: `http://127.0.0.1:8001/health`
- Swagger: `http://127.0.0.1:8001/docs`

Expected health response:

```json
{"status":"ok"}
```

### Terminal 2 — payer-aware frontend (recommended for current billing flow)

```powershell
Remove-Item Env:CLINICAL_API_URL -ErrorAction SilentlyContinue
python -m streamlit run frontend/streamlit1_app.py --server.port 8503
```

Open `http://127.0.0.1:8503/`.

### Optional — original frontend

To run the original UI instead, use:

```powershell
Remove-Item Env:CLINICAL_API_URL -ErrorAction SilentlyContinue
python -m streamlit run frontend/streamlit_app.py --server.port 8502
```

Open `http://127.0.0.1:8502/`. Both frontends use the shared API unless
`CLINICAL_API_URL` is set to another backend.

### Use a different backend port

If port 8001 is already occupied, choose a free port and set it in both
processes:

```powershell
# Backend terminal
$env:PORT = "8003"
python -m app
```

```powershell
# Frontend terminal
$env:CLINICAL_API_URL = "http://127.0.0.1:8003"
python -m streamlit run frontend/streamlit1_app.py --server.port 8503
```

Do not point a frontend at an unrelated API that happens to respond on the
selected port. Verify `/openapi.json` identifies this project as
`Clinical Decision Support API` and `/health` returns `{"status":"ok"}`.

## API reference

Routes are implemented in `app/api/routes.py`; request models are in
`app/api/schemas.py`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | API name and links |
| `GET` | `/health` | Liveness response |
| `GET` | `/patients` | Patient list with active payer and plan joined from Insurance |
| `GET` | `/patients/export` | Export the EHR JSON payload |
| `GET` | `/patients/{patient_id}` | Patient chart, patient details, active payer, and patient-linked sheets |
| `POST` | `/patients` | Create a patient and optional initial payer coverage |
| `PUT` | `/patients/{patient_id}` | Update patient demographics |
| `PUT` | `/patients/{patient_id}/records/{sheet_name}` | Replace editable records in a patient sheet |
| `POST` | `/patients/{patient_id}/records/{sheet_name}` | Add a patient-linked record |
| `DELETE` | `/patients/{patient_id}` | Delete patient and linked records (API only; not exposed in the UIs) |
| `POST` | `/patients/{patient_id}/bills/ipd` | Generate and save a bill using the selected payer and encounter |
| `POST` | `/codes` | Return ICD-10-CM and CPT/HCPCS code suggestions |
| `POST` | `/encounters/scribe` | Generate SOAP/context without running CDS |
| `POST` | `/encounters/cds` | Add CDS to a submitted encounter context |
| `POST` | `/encounters/analyze` | Run the scribe and CDS combined flow |
| `POST` | `/encounters/analyze-audio` | Backend-side audio transcription and encounter analysis |
| `POST` | `/analyze` | Legacy patient-summary analysis |
| `POST` | `/retrieve` | Retrieve local knowledge-base context |

Example bill request (the patient and encounter must exist):

```powershell
$body = @{
    cpt_hcpcs_codes = @("99213")
    icd10_codes = @()
    encounter_id = "ENC00504"
    encounter_type = "Outpatient"
    payer_name = "Athena Health Insurance"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri http://127.0.0.1:8001/patients/P100052/bills/ipd `
  -Method Post `
  -ContentType "application/json" `
  -Body $body
```

The only supported `payer_name` is `Athena Health Insurance`. If
`encounter_id` is provided, it must belong to the URL's patient. The API
returns useful HTTP errors for missing patients/encounters, invalid input, and
unavailable source files.

## Knowledge base and CDS evidence

The source document is
`knowledge_base/knowledge_base_harrison.docx`. Build or rebuild the local
retrieval artifacts after changing the source document or when the index is
missing:

```powershell
python -m app.scripts.build_kb
```

The retriever uses the configured local index, removes highly overlapping
chunks, and includes source/section labels in retrieved context. The CDS agent
requires the model to cite at least one retrieved source when source labels are
available. If no relevant passage is returned, the prompt must say so instead
of implying the source supports a recommendation.

Optional retrieval configuration in `.env`:

```dotenv
EMBEDDING_BACKEND=auto
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_BATCH_SIZE=64
RETRIEVAL_CANDIDATES=20
RETRIEVAL_RESULTS=5
MAX_CONTEXT_CHARS=12000
```

`EMBEDDING_BACKEND=auto` uses Sentence Transformers when available and the
project's TF-IDF artifacts as a fallback during index construction. Use
`EMBEDDING_BACKEND=tfidf` to request TF-IDF explicitly. Scanned images embedded
in Word documents are not OCR'd by `python-docx`; scanned source text must be
made extractable before indexing.

## Tests

From `Clinical_decision_support`, run:

```powershell
python -m pytest app/tests -q
```

Focused tests:

```powershell
python -m pytest app/tests/test_agents.py app/tests/test_api.py -q
python -m pytest app/tests/test_streamlit1_app.py app/tests/test_api_client.py -q
python -m pytest app/tests/test_record_store.py app/tests/test_rag.py -q
```

Tests mock external services where appropriate; a passing test suite does not
prove that an actual API key, external LLM endpoint, Google transcription, or
production clinical evidence source is available.

## Troubleshooting

### Frontend says it cannot connect to FastAPI

1. Start the backend with `python -m app` from this project directory.
2. Check `http://127.0.0.1:8001/health`.
3. Confirm that the frontend's `CLINICAL_API_URL` is unset or points to the
   backend actually running this project.
4. If the backend uses another port, set the same URL in the frontend terminal.

### Port is already in use

Choose a free backend port using `PORT` and set `CLINICAL_API_URL` accordingly.
For Streamlit, pass a free port with `--server.port`. Avoid changing ports in
only one terminal.

### `No module named ...` at startup

The process is likely using a different Python environment from the one where
dependencies were installed. Activate this project's `.venv` in each terminal
or invoke `.venv\Scripts\python.exe` directly.

### SOAP or CDS is unavailable

Check `.env`, restart the backend after changing it, verify network access to
the configured LLM endpoint, and review the displayed error/status metadata.
CDS also requires built retrieval artifacts and a non-empty knowledge base.
The system preserves the transcript when the scribe cannot structure it and
does not replace a failed evidence-grounded CDS request with generic advice.
Re-running a request is not a guarantee of provider availability.

### A patient still shows “Payer not assigned”

The payer displayed on a patient comes from the active `Insurance` record. In
the alternate billing flow, payer assignment is persisted only when a bill is
generated and saved with a payer. **Validate Bill** checks the saved bill; it
does not assign coverage. Refresh/reload EHR data after generating the bill.

### Patient selector does not show the expected payer-aware workflow

Use the alternate frontend at `http://127.0.0.1:8503/` and confirm it was
started with:

```powershell
python -m streamlit run frontend/streamlit1_app.py --server.port 8503
```

The 8502 UI is the original frontend and does not have the same payer-aware
bill flow.

## Project map

```text
Clinical_decision_support/
  app/
    agent/                 Encounter, scribe, and CDS orchestration
    api/                   FastAPI routes and Pydantic request/response models
    core/                  Environment and runtime configuration
    rag/                   Knowledge ingestion, embeddings, and retrieval
    scripts/build_kb.py    Build local CDS retrieval artifacts
    services/              EHR, billing, transcription, and legacy analysis
    tests/                 API, agent, billing, frontend-helper, and RAG tests
  frontend/
    streamlit_app.py       Original Streamlit UI (default port 8502)
    streamlit1_app.py      Athena billing UI (port 8503)
    api_client.py          Shared HTTP client for FastAPI
    clinical_workflow.py   Patient linking, coding, bill eligibility/validation
    record_store.py        Local consultation JSON validation and persistence
    theme.css              Alternate frontend presentation
    ui_theme.py            Theme selection and application
  knowledge_base/          Source documents for CDS evidence
  chroma_db/               Generated local retrieval/index artifacts
  clinical_records.json    Local consultation workspace store
```

Related files in the parent repository directory:

- `Epic_Inspired_USA_50_Patient_Demo_25_Athena_25_Care_Gap.json`: active EHR
  demo dataset; all current coverage is Athena.
- `Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json`: authoritative
  Athena-only billing and charge-preview source. There is no combined-payer
  fallback. Its synthetic 71045 and 82947 OPD demo estimates are explicitly
  labeled and are not verified contract rates.
- `Epic_Inspired_USA_50_Patient_Demo.json`: original/legacy EHR demo data.

## Changes represented in the current implementation

The current implementation includes the following user-facing and technical
changes:

- Added an alternate Athena billing workflow without replacing the original
  Streamlit entry point.
- Added the 50-patient EHR demo dataset and IPD/OPD charge sheets; John F.
  Kennedy and Donald Trump are separately addressable
  patients after the original 50.
- Consolidated current active coverage and new billing to Athena; older
  historical bills retain their original payer for audit.
- Saved a generated bill, its lines, and draft claim rows to the EHR data, and
  kept the hospital account stable for repeat bills on the same encounter.
- Saved the selected bill payer as active synthetic primary coverage so the
  EHR patient selector and Patient Master view show the payer after bill
  generation.
- Added explicit validation of bill patient, encounter, payer, priced lines,
  and gross-total arithmetic. Validation is not claim submission.
- Added an explicit performed-and-documented service confirmation before
  generating a bill.
- Added evidence citation requirements, bounded retries, and clear failure
  behavior for SOAP/CDS generation rather than success-shaped clinical
  fallbacks.
- Added patient-link checks, atomic local consultation persistence, improved
  theme styling, and a visible green sidebar control in the alternate UI.
- Documented synthetic-data, privacy, and clinical/billing limitations.

## Limitations and safety

- **No real PHI:** frontend recording uses an external transcription service and
  the backend sends encounter text to the configured LLM provider. This demo
  has no authentication, authorization, role controls, encryption-at-rest
  design, or production privacy controls.
- **No production EHR:** JSON files are not a transactional multi-user database.
  Concurrent writers, tamper-evident audit, disaster recovery, and a
  production-grade backup policy are not implemented.
- **No clinical guarantee:** SOAP is model-assisted or transcript-only
  fallback; CDS depends on source quality, retrieval quality, and model
  availability. A clinician must review all content and supporting evidence.
- **No final coding determination:** ICD-10-CM and CPT/HCPCS suggestions can be
  incomplete or wrong. A qualified coder/clinician must confirm diagnosis,
  code, units, documentation, and service performance.
- **No insurance processing:** payer assignments and cost shares are synthetic
  demo values. There is no eligibility, benefits, authorization, claim
  adjudication, claim submission, or payment processing.
- **Health endpoint scope:** `/health` is a liveness check only; it does not
  validate LLM configuration, knowledge-base availability, EHR contents, or
  charge sheet readability.
