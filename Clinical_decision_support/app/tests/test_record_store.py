import json

import pytest

from frontend import record_store


def test_record_store_round_trips_records_atomically(tmp_path):
    path = tmp_path / "clinical_records.json"
    records = [{"id": "visit-1", "name": "Demo Patient"}]

    record_store.save_records(path, records)

    assert record_store.load_records(path) == records
    assert not path.with_name(f"{path.name}.tmp").exists()


def test_record_store_reports_corrupt_json_instead_of_returning_empty_records(tmp_path):
    path = tmp_path / "clinical_records.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="could not be read"):
        record_store.load_records(path)


def test_record_store_rejects_invalid_record_shape(tmp_path):
    path = tmp_path / "clinical_records.json"
    path.write_text(json.dumps({"records": ["not a record"]}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="list of record objects"):
        record_store.load_records(path)


def test_failed_save_preserves_previous_file_and_cleans_temporary_file(
    monkeypatch, tmp_path
):
    path = tmp_path / "clinical_records.json"
    original = '{"records":[{"id":"existing"}]}'
    path.write_text(original, encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(record_store.os, "replace", fail_replace)

    with pytest.raises(RuntimeError, match="could not be saved"):
        record_store.save_records(path, [{"id": "new"}])

    assert path.read_text(encoding="utf-8") == original
    assert not path.with_name(f"{path.name}.tmp").exists()
