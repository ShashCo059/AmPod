# EHR Demo Data and Billing

This guide describes the synthetic EHR data and draft-billing features in the
Streamlit application. The data and charges are for demonstration only; they
are not a live EHR, a claim submission system, or a source of actual patient
financial responsibility.

## Where to find the features

Start the FastAPI backend and Streamlit frontend as described in
[README.md](./README.md). In the Streamlit sidebar:

- **EHR Demo Data** provides patient charts, record editing, bill history, and
  Athena IPD and OPD charge-sheet previews.
- **Codes** provides clinician-reviewed code suggestions and the option to
  generate a draft hospital bill for an EHR patient and encounter.

## EHR demo data

### Data source and structure

The backend reads and writes
`Epic_Inspired_USA_50_Patient_Demo_25_Athena_25_Care_Gap.json` in the repository
workspace root. All current and newly added patients use Athena Health Insurance. Older
historical bills retain the payer recorded when each bill was created. The
JSON document contains a `sheets` object; each sheet has a structure, headers,
and records. Patient records are linked across sheets using `Patient_ID`. The
`Patient_Master` sheet contains demographics; other record sheets include
encounters, problems, allergies, medications, vitals, labs, orders and
procedures, clinical notes, coverage, and billing history. The source file may
have more or fewer than 50 patients as patients are added or removed.

The **EHR Demo Data** page lets users:

- Browse a patient's chart and switch between record types.
- Edit patient demographics or linked records and save the changes.
- Review the selected patient's problems, allergies, medications, vitals, labs,
  and orders alongside code suggestions in the Codes workflow.
- Draft a neutral physician documentation query with its clinical indicators,
  optional response choices, and a locally recorded response. Queries are not
  sent to a provider or written into the EHR.
- Add a patient with a first name, last name, and date of birth. The backend
  assigns a patient ID and MRN.
- Browse patients with their active payer shown in the patient directory.
- Download the current EHR JSON.
- Inspect a read-only per-patient audit log with before/after snapshots for
  demographic and linked-record edits.

Writes are made by the backend to the JSON file. The backend writes through a
temporary sibling file before replacing the source file. Treat this demo file
as mutable application data: back it up before testing edits.

### EHR API

The Streamlit client uses these FastAPI routes:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/patients` | List patients |
| `GET` | `/patients/export` | Export the complete workbook-shaped JSON |
| `GET` | `/patients/{patient_id}` | Load a patient and linked chart records |
| `POST` | `/patients` | Add a patient |
| `PUT` | `/patients/{patient_id}` | Update patient demographics |
| `PUT` | `/patients/{patient_id}/records/{sheet_name}` | Replace a patient's rows in an editable record sheet |
| `POST` | `/patients/{patient_id}/records/{sheet_name}` | Add a row to an editable record sheet |

`Patient_Master` is edited through the patient update route, not the record
sheet routes. The API validates patient existence, required names, and ISO
dates of birth. Patient deletion is not exposed by either Streamlit interface.
The audit log is appended by the backend when saved patient data changes and
cannot be edited through the generic record-replacement endpoint. It uses a
demonstration actor label and is not a tamper-proof or authenticated audit
system.

## Draft billing

### Pricing source

Athena bill generation reads
`Hospital_IPD_OPD_Charges_Athena_Only_Expanded.json` from the repository
workspace root. It uses the Athena `IPD Charges` or `OPD Charges` records to
find gross unit charges, expected allowed amounts, patient responsibility, and
contractual adjustments for each CPT/HCPCS code. There is no fallback to the
combined-payer workbook. The original
`Original_Hospital_IPD_OPD_Charges_200_Demo.xlsx` remains available to the
legacy, non-payer path only. The EHR JSON stores the resulting bills and line
items.

The **Athena Charges** tab previews Athena rows from the inpatient and
outpatient sheets. If the source JSON is missing, malformed, or does not have
the expected sheets and records, bill generation fails with an explicit error.

The OPD JSON includes explicitly labeled synthetic estimates for CPT `71045`
(one-view chest X-ray: $140 gross, $61.78 allowed, $12.36 patient
responsibility) and CPT `82947` (blood glucose: $45 gross, $22.11 allowed,
$4.42 patient responsibility). These are demo-only rates, not verified
contract terms and not suitable for real claims.

### Generate a bill from a consultation

1. Open a consultation's **Codes** page and review its code suggestions.
2. Select the EHR patient. A consultation already linked to a patient in the
   active EHR dataset selects that patient automatically. If its saved patient
   link is missing or refers to a patient outside the active dataset, create an
   EHR patient from the consultation or register it against the intended
   existing patient first. Billing remains disabled until the consultation is
   linked; it will not silently use another patient's encounters or procedures.
3. Choose an existing patient encounter or create a new one. For a new
   encounter, choose **Outpatient** or **Inpatient**.
4. The payer is fixed to **Athena Health Insurance** for every patient. Use
   the consultation's CPT/HCPCS codes only when they belong to the selected
   patient's encounter. If they do not, use the encounter's EHR procedure
   orders instead.
5. Select **Generate Bill**. The application saves a draft bill using Athena's
   IPD or OPD rates and displays gross charges, expected allowed amount,
   estimated patient responsibility, and line items. A complete bill already
   saved for the same patient, encounter, payer, and CPT set is rejected in
   both the UI and API (HTTP 409); incomplete legacy bills do not block a
   corrected replacement.
6. Select **Validate Bill** to check the generated bill's patient, encounter,
   payer, priced lines, and gross total. This is a local consistency check; it
   does not submit a claim or change the draft's status.

### Clinical review decisions and audit evidence

The Clinical Review page shows the source transcript, generated SOAP note,
retrieved guidance, and generation metadata. A clinician can mark a non-empty
CDS recommendation **Accepted** or **Overridden**. An override requires a
rationale. The decision, rationale, timestamp, and before/after values are
saved with the local consultation record and displayed in its audit evidence
panel. Saving a draft or finalizing a note also records the changed summary,
transcript entries, and status. These entries support demonstration and
review; they do not authenticate a reviewer or replace a compliant clinical
audit trail.

Consultation links are checked against both the EHR patient ID and patient
name. If a saved link is missing or points to a different named patient, the
Codes page will not bill against that patient's encounters or procedure
orders. It offers a link repair when a matching EHR patient exists, or a
matching-patient creation action when one does not.

Generating a bill creates a new bill ID and draft. The UI and API reject a
second complete bill for the same patient, encounter, payer, and CPT set;
incomplete legacy bills do not block a corrected replacement. The check and
save are serialized within one backend process, but the JSON store does not
provide cross-process transactional locking. Bills for the same EHR encounter
retain that encounter's hospital account number. Previously generated bills
remain in Bill History.

If no encounter is selected, bill generation creates a billing encounter in
the EHR. If an existing encounter is selected, its type determines whether
inpatient or outpatient pricing applies.

### How bill amounts are calculated

- Procedure codes from the selected encounter's `Orders_Procedures` rows are
  included with their recorded quantities. Repeated order quantities for a
  code are added together.
- CPT/HCPCS codes supplied from the reviewed consultation are included with a
  quantity of one unless that code already has an order quantity.
- For Athena bills, the application uses matching rows in the Athena-only
  JSON's encounter type IPD or OPD sheet. It calculates gross charges,
  expected allowed amounts, patient responsibility, and contractual
  adjustments separately. Formula values in the JSON are evaluated only for
  the known 20% patient-share and gross-minus-allowed adjustment formulas.
  These are synthetic estimates, not adjudicated amounts.
- The API defaults to Athena and rejects unsupported payer values.
- For each priced line, gross charge is unit charge multiplied by quantity.
  If any requested code is unpriced, bill generation is rejected; it does not
  save a partial bill.
- If the Athena charge source has no rate for a code, the Codes page displays
  a revenue-leakage review warning. Missing prices are not treated as
  zero-dollar services.
- ICD-10 codes are stored with the bill as diagnosis metadata. They do not add
  charge lines.

### Persisted billing records

Generated bills are stored in the same EHR JSON document:

- `Generated_Bills` contains bill summaries, including bill ID, patient,
  encounter, type, source, codes, status, unpriced codes, line count, and gross
  total.
- `Generated_Bill_Lines` contains the bill's individual priced charges,
  quantities, unit rates, departments, and gross line totals.
- If present, `Billing_Claims` receives corresponding claim rows with **Draft**
  status. These rows are demo records, not submitted insurance claims.
- A newly created billing encounter is stored in `Encounters`.

The **Bill History** tab displays generated bills and historical claim rows,
allows filtering and searching, and supports CSV download. Select a generated
bill to view its saved charge-line detail. Historical claims are shown as
historical records and are not regenerated into new bills.

## API for draft bills

`POST /patients/{patient_id}/bills/ipd` generates and saves a bill. Example
request:

```json
{
  "icd10_codes": ["I10"],
  "cpt_hcpcs_codes": ["83036", "99213"],
  "encounter_id": "ENC00001",
  "encounter_type": "Outpatient",
  "payer_name": "Athena Health Insurance"
}
```

`encounter_id` may be omitted to create a new billing encounter. The
`encounter_type` is used when no existing encounter is selected.
`payer_name` is fixed to `Athena Health Insurance`. A successful response includes `Bill_ID`,
`Encounter_ID`, `Line_Items`, `Unpriced_Codes`, `Gross_Total_USD`,
`Expected_Allowed_Total_USD`, `Patient_Responsibility_Total_USD`, and a notice
describing the synthetic estimates.

Typical errors include:

- `404`: patient or specified encounter does not exist.
- `409`: a complete bill already exists for this patient, encounter, payer,
  and code set.
- `422`: encounter does not belong to the patient, there are no billable
  procedure codes, or none of the codes have prices.
- `503`: chargemaster source is missing or unavailable.

## Important limitations

- Gross charges are demo chargemaster charges. Payer allowed amounts,
  contractual adjustments, and patient responsibility are estimates from the
  synthetic charge sheets; they are not final coverage, adjudication, copays,
  deductibles, payer payments, or final patient responsibility.
- Bills and claims remain drafts. Nothing is submitted to a payer or external
  billing system.
- The workbook and EHR data are synthetic demonstration assets. CPT/HCPCS
  suggestions and charges must not be treated as validated coding or current
  market rates.
- A clinician or qualified coder must validate the encounter, patient, service,
  codes, and quantities before any real-world billing use.
- Patient deletion is intentionally hidden from the Streamlit interfaces.
  The backend still contains a DELETE endpoint for controlled test/setup use;
  do not call it against data that should be retained.
