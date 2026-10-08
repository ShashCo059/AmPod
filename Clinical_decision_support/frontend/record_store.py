import json
import os
from pathlib import Path
from typing import Any


def load_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Clinical records could not be read from {path}: {error}") from error

    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise RuntimeError(
            f"Clinical records in {path} must contain a list of record objects."
        )
    return records


def save_records(path: Path, records: list[dict[str, Any]]) -> None:
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump({"records": records}, file, indent=4, ensure_ascii=False)
            file.write("\n")
        os.replace(temporary_path, path)
    except OSError as error:
        raise RuntimeError(f"Clinical records could not be saved to {path}: {error}") from error
    finally:
        temporary_path.unlink(missing_ok=True)
