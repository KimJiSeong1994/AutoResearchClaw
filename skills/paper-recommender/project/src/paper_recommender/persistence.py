from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # pragma: no cover - fcntl is unavailable on non-POSIX platforms.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace ``path`` with fsync'd UTF-8 text."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        _fsync_parent_dir(path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@contextmanager
def locked_paths(*paths: Path) -> Iterator[None]:
    """Serialize readers/writers that share append-only runtime state.

    The sidecar lock keeps independent JSONL files safe when a job is invoked
    by cron while an operator is also running it manually. On platforms without
    ``fcntl`` this still creates the lock path and degrades to ordinary append.
    """

    if not paths:
        raise ValueError("locked_paths requires at least one path")
    lock_path = _lock_path(paths)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON object as a durable JSONL row."""

    with locked_paths(path):
        append_jsonl_record_unlocked(path, record)


def append_jsonl_record_unlocked(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    if not existed:
        _fsync_parent_dir(path)


def _fsync_parent_dir(path: Path) -> None:
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    with locked_paths(path):
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if isinstance(raw, dict):
                rows.append(raw)
        return rows


def _lock_path(paths: tuple[Path, ...]) -> Path:
    first = paths[0]
    joined = "--".join(p.name for p in paths)
    return first.parent / f".{joined}.lock"
