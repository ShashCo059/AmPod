# Coding Agent Integration Pack

This document tells you exactly which files to send to an older Agent Pod repo that only has the CDS agent, so the newer coding agent logic can be added without copying the whole project.

## What to send

Copy the following files/folders from this repo into the target repo:

### Core coding engine
- `hcc_engine/code_matcher.py`

### Data conversion and source datasets
- `convert_icd10_to_csv.py`
- `icd10.csv`
- `ICD10CM_2022_Codes.json`
- `CPT_CODES.json`
- `structured_cpt_hcpcs_2026.json` (optional, if available in your newer repo)

### Frontend integration point
- `Clinical_decision_support/frontend/streamlit_app.py`

### Optional supporting files
- `test_icd10_matcher.py` — for sanity-check verification

## Minimum merge strategyfif

If you want the smallest possible transfer, send these only:

1. `hcc_engine/code_matcher.py`
2. `convert_icd10_to_csv.py`
3. `icd10.csv`
4. `CPT_CODES.json`
5. The exact frontend block that calls `get_medical_codes(...)` and renders `st.dataframe(...)`

That is enough to add diagnosis and procedure coding to a repo that already has CDS/clinical note generation.

## Folder layout to create in the older repo

Create a folder like this:

```text
<your older agent pod repo>/
├── hcc_engine/
│   ├── __init__.py
│   └── code_matcher.py
├── icd10.csv
├── CPT_CODES.json
├── ICD10CM_2022_Codes.json
├── convert_icd10_to_csv.py
└── Clinical_decision_support/
    └── frontend/
        └── streamlit_app.py
```

## Important integration rule

This coding logic should not be applied to every insight blindly. The matcher is designed to split items by clinical type:

- diagnosis / symptom / disease / injury -> ICD-10
- procedure / test / imaging / therapy -> CPT/HCPCS

This avoids hallucinated CPT matches for entries like fever, hypertension, asthma, diabetes, etc.

## Copy-paste integration snippet

Add this to the review page or any clinical insight display section:

```python
from hcc_engine.code_matcher import get_medical_codes, classify_medical_item

all_code_candidates = [item for item in insights if item]

icd_items = [item for item in all_code_candidates if classify_medical_item(item) == "icd10"]
cpt_items = [item for item in all_code_candidates if classify_medical_item(item) == "cpt"]

icd_matches = get_medical_codes(icd_items, threshold=90, code_type="icd10") if icd_items else []
cpt_matches = get_medical_codes(cpt_items, threshold=90, code_type="cpt") if cpt_items else []

st.markdown('<div class="insight-heading">Suggested ICD-10 Diagnosis Codes</div>', unsafe_allow_html=True)
if icd_matches:
    icd_df = pd.DataFrame(icd_matches)
    st.dataframe(icd_df, hide_index=True, use_container_width=True)
else:
    st.caption("No close ICD-10 matches found for these conditions.")

st.markdown('<div class="insight-heading">Suggested CPT/HCPCS Procedure Codes</div>', unsafe_allow_html=True)
if cpt_matches:
    cpt_df = pd.DataFrame(cpt_matches)
    st.dataframe(cpt_df, hide_index=True, use_container_width=True)
else:
    st.caption("No procedure-specific CPT/HCPCS matches found.")
```

## How to generate the CSV if the target repo does not already have it

From the target repo root, run:

```bash
python convert_icd10_to_csv.py
```

This converts the ICD JSON into `icd10.csv` for fuzzy matching.

## Recommended verification step

Once copied, run a quick Python smoke test:

```bash
python -c "from hcc_engine.code_matcher import get_medical_codes; print(get_medical_codes(['Hypertension', 'Tuberculosis', 'Chest x-ray 2 views', 'Complete blood count'], threshold=90))"
```

Expected behavior:
- `Hypertension` and `Tuberculosis` should return ICD-10 matches
- `Chest x-ray 2 views` and `Complete blood count` should return CPT/HCPCS matches
- generic symptoms should not accidentally generate CPT codes

## Recommended zip package to send

If your friend is on another machine, send the following as a zip:

```text
agent_pod_coding_agent_pack/
├── hcc_engine/
│   ├── __init__.py
│   └── code_matcher.py
├── convert_icd10_to_csv.py
├── icd10.csv
├── ICD10CM_2022_Codes.json
├── CPT_CODES.json
├── Clinical_decision_support/
│   └── frontend/
│       └── streamlit_app.py
└── README_INTEGRATION.txt
```

## Final recommendation

The cleanest handoff is to send the coding logic and dataset bundle, not the whole app. The old repo can keep its CDS and agent architecture, and just receive the medical coding engine plus the UI rendering patch.

This keeps the integration small, safer, and easier to review.
