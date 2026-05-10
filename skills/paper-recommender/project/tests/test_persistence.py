from __future__ import annotations

import json

from paper_recommender import persistence
from paper_recommender.persistence import (
    append_jsonl_record,
    atomic_write_text,
    locked_paths,
    read_jsonl_records,
)


def test_append_jsonl_record_creates_fsynced_line_and_lock(tmp_path) -> None:
    path = tmp_path / "state" / "runtime_events.jsonl"

    append_jsonl_record(path, {"event": "started", "run_id": "r1"})
    append_jsonl_record(path, {"event": "completed", "run_id": "r1"})

    assert read_jsonl_records(path) == [
        {"event": "started", "run_id": "r1"},
        {"event": "completed", "run_id": "r1"},
    ]
    assert (path.parent / ".runtime_events.jsonl.lock").exists()


def test_atomic_write_text_replaces_existing_file(tmp_path) -> None:
    path = tmp_path / "state" / "last_run_status.json"

    atomic_write_text(path, json.dumps({"status": "old"}))
    atomic_write_text(path, json.dumps({"status": "new"}))

    assert json.loads(path.read_text()) == {"status": "new"}


def test_read_jsonl_records_participates_in_sidecar_lock(tmp_path) -> None:
    path = tmp_path / "state" / "runtime_events.jsonl"
    append_jsonl_record(path, {"event": "started", "run_id": "r1"})

    calls: list[int] = []

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_UN = 2

        @staticmethod
        def flock(fd, operation):
            calls.append(operation)

    original_fcntl = persistence.fcntl
    persistence.fcntl = FakeFcntl
    try:
        assert read_jsonl_records(path) == [{"event": "started", "run_id": "r1"}]
    finally:
        persistence.fcntl = original_fcntl

    assert calls == [FakeFcntl.LOCK_EX, FakeFcntl.LOCK_UN]


def test_locked_paths_rejects_empty_path_set() -> None:
    try:
        with locked_paths():
            pass
    except ValueError as exc:
        assert str(exc) == "locked_paths requires at least one path"
    else:
        raise AssertionError("expected ValueError")
