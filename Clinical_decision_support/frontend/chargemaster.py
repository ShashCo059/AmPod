from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
SUPPORTED_FILES = (
    DATA_DIR / "chargemaster.csv",
    DATA_DIR / "chargemaster.xlsx",
    DATA_DIR / "Demo_Hospital_Chargemaster_CDM.xlsx",
    PROJECT_ROOT.parent / "Demo_Hospital_Chargemaster_CDM.xlsx",
)


def _normalized_columns(frame: pd.DataFrame) -> dict[str, str]:
    return {
        "".join(character for character in str(column).lower() if character.isalnum()): column
        for column in frame.columns
    }


def _find_column(columns: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in columns:
            return columns[name]
    return None


def _read_source(source: Path) -> pd.DataFrame:
    if source.suffix.lower() == ".csv":
        return pd.read_csv(source, dtype=str)

    preview = pd.read_excel(source, sheet_name="CDM Master", header=None, dtype=str)
    header_index = next(
        (
            index
            for index, row in preview.iterrows()
            if any("cpt/hcpcs" in str(value).lower() for value in row.tolist())
        ),
        None,
    )
    if header_index is None:
        raise ValueError("Chargemaster workbook does not contain a CPT/HCPCS header.")
    return pd.read_excel(source, sheet_name="CDM Master", header=header_index, dtype=str)


def load_chargemaster() -> pd.DataFrame:
    """Load the optional hospital chargemaster from data/ as a normalized frame."""
    source = next((path for path in SUPPORTED_FILES if path.exists()), None)
    if source is None:
        return pd.DataFrame(columns=["code", "rate", "quantity", "service_date"])

    frame = _read_source(source)
    columns = _normalized_columns(frame)
    code_column = _find_column(columns, ("cpt", "cptcode", "hcpcs", "hcpcscode", "cpthcpcs", "procedurecode", "code"))
    rate_column = _find_column(columns, ("rate", "charge", "standardcharge", "grossunitcharge", "unitprice", "price", "amount"))
    quantity_column = _find_column(columns, ("quantity", "qty", "quantityunit", "units", "unit", "unitbasis"))
    date_column = _find_column(columns, ("date", "servicedate", "effectivedate"))

    if code_column is None:
        raise ValueError("Chargemaster must contain a CPT or HCPCS code column.")

    normalized = pd.DataFrame()
    normalized["code"] = frame[code_column].fillna("").astype(str).str.strip().str.upper()
    normalized["rate"] = frame[rate_column].fillna("").astype(str).str.strip() if rate_column else ""
    normalized["quantity"] = frame[quantity_column].fillna("1").astype(str).str.strip() if quantity_column else "1"
    normalized["service_date"] = frame[date_column].fillna("").astype(str).str.strip() if date_column else ""
    valid_codes = normalized["code"].str.match(r"^(?:\d{5}|[A-Z]\d{4})$", na=False)
    return normalized[valid_codes].drop_duplicates("code", keep="first")


def build_cpt_bill_rows(cpt_results: list[dict[str, Any]], encounter_date: str | None = None) -> list[dict[str, str]]:
    """Combine matched CPT codes with chargemaster quantity and rate fields."""
    try:
        chargemaster = load_chargemaster().set_index("code")
    except (OSError, ValueError, ImportError):
        chargemaster = pd.DataFrame(columns=["rate", "quantity", "service_date"])

    default_date = encounter_date or date.today().strftime("%d/%m/%Y")
    rows = []
    for result in cpt_results:
        code = str(result.get("CPT/HCPCS Code", "")).strip().upper()
        extracted = str(result.get("Extracted Procedure", ""))
        procedure = str(result.get("Matched Procedure/Service", ""))
        if "established patient office visit" in procedure.lower() and "99214" in chargemaster.index:
            code = "99214"
        if (
            code == "71045"
            and "71046" in chargemaster.index
            and "chest" in extracted.lower()
            and not any(view in extracted.lower() for view in ("1 view", "one view", "2 views", "two views", "1-2"))
        ):
            code = "71046"
        if (
            code not in chargemaster.index
            and "blood test" in extracted.lower()
            and "glucose" not in extracted.lower()
            and "85025" in chargemaster.index
        ):
            code = "85025"
        charge = chargemaster.loc[code] if code in chargemaster.index else None
        rows.append(
            {
                "Date": default_date,
                "CPT": code,
                "Quantity/Unit": str(charge["quantity"]).strip() if charge is not None else "1",
                "Estimated Rate": str(charge["rate"]).strip() if charge is not None else "Not in chargemaster",
                "Procedure": procedure,
            }
        )
    return rows
