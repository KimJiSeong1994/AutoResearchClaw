from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from paper_recommender.persistence import append_jsonl_record, atomic_write_text


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StateStore:
    root: Path

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def seen_path(self) -> Path:
        return self.root / "seen.json"

    @property
    def profile_path(self) -> Path:
        return self.root / "profile.json"

    @property
    def narrative_path(self) -> Path:
        return self.root / "profile.md"

    @property
    def run_log_path(self) -> Path:
        return self.root / "runs.jsonl"

    @property
    def runtime_events_path(self) -> Path:
        return self.root / "runtime_events.jsonl"

    @property
    def ab_log_path(self) -> Path:
        return self.root / "ab_log.jsonl"

    @property
    def souls_dir(self) -> Path:
        return self.root / "souls"

    @property
    def feedback_log_path(self) -> Path:
        return self.root / "feedback_log.jsonl"

    @property
    def weekly_seen_path(self) -> Path:
        return self.root / "weekly_seen.json"

    @property
    def weekly_report_log_path(self) -> Path:
        return self.root / "weekly_reports.jsonl"

    def feedback_inbox_dir(self, subdir: str) -> Path:
        return self.root / subdir

    def load_processed_feedback_keys(self) -> set[tuple]:
        if not self.feedback_log_path.exists():
            return set()
        seen: set[tuple] = set()
        try:
            for line in self.feedback_log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                title = (entry.get("title") or "").strip().lower()
                seen.add((
                    entry.get("paper_id"),
                    title,
                    entry.get("kind"),
                    entry.get("reason"),
                    entry.get("note_date"),
                ))
        except OSError:
            return set()
        return seen

    def append_processed_feedback(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        ts = _utcnow().isoformat(timespec="seconds")
        for r in records:
            payload = {**r, "processed_at": ts}
            append_jsonl_record(self.feedback_log_path, payload)

    @property
    def soul_meta_path(self) -> Path:
        return self.root / "soul_meta.json"

    def soul_path(self, user_id: str) -> Path:
        # user_id must already be sanitized (see paper_recommender.auth).
        return self.souls_dir / f"{user_id}.md"

    def load_soul(self, user_id: str) -> str | None:
        p = self.soul_path(user_id)
        if not p.exists():
            return None
        return p.read_text(encoding="utf-8")

    def save_soul(self, user_id: str, md: str) -> None:
        self.souls_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.soul_path(user_id), md)

    def load_soul_meta(self) -> dict[str, Any]:
        if not self.soul_meta_path.exists():
            return {}
        try:
            return json.loads(self.soul_meta_path.read_text() or "{}")
        except json.JSONDecodeError:
            return {}

    def save_soul_meta(self, meta: dict[str, Any]) -> None:
        atomic_write_text(self.soul_meta_path, json.dumps(meta, ensure_ascii=False, indent=2))

    def last_soul_update(self, user_id: str) -> str | None:
        entry = self.load_soul_meta().get(user_id, {})
        return entry.get("last_update") if isinstance(entry, dict) else None

    def soul_last_bookmark_id(self, user_id: str) -> str | None:
        entry = self.load_soul_meta().get(user_id, {})
        return entry.get("last_bookmark_id") if isinstance(entry, dict) else None

    def bump_soul_update(
        self,
        user_id: str,
        last_bookmark_id: str | None = None,
    ) -> None:
        meta = self.load_soul_meta()
        entry = meta.setdefault(user_id, {})
        entry["last_update"] = _utcnow().isoformat(timespec="seconds")
        if last_bookmark_id is not None:
            entry["last_bookmark_id"] = last_bookmark_id
        self.save_soul_meta(meta)

    def load_seen(self) -> dict[str, str]:
        if not self.seen_path.exists():
            return {}
        raw = self.seen_path.read_text() or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def is_recently_seen(self, paper_id: str, cooldown_days: int) -> bool:
        seen = self.load_seen()
        ts = seen.get(paper_id)
        if not ts:
            return False
        try:
            seen_day = date.fromisoformat(ts[:10])
        except ValueError:
            return False
        return (date.today() - seen_day) < timedelta(days=cooldown_days)

    def record_seen(self, paper_ids: list[str]) -> None:
        seen = self.load_seen()
        today = date.today().isoformat()
        for pid in paper_ids:
            seen[pid] = today
        atomic_write_text(self.seen_path, json.dumps(seen, ensure_ascii=False, indent=2))

    def gc_seen(self, cooldown_days: int) -> None:
        seen = self.load_seen()
        cutoff = date.today() - timedelta(days=cooldown_days * 2)
        pruned = {k: v for k, v in seen.items() if _parse_day(v) and _parse_day(v) >= cutoff}
        atomic_write_text(self.seen_path, json.dumps(pruned, ensure_ascii=False, indent=2))

    def load_profile(self, ttl_days: int) -> dict[str, Any] | None:
        if not self.profile_path.exists():
            return None
        raw = self.profile_path.read_text() or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        built_at = data.get("built_at")
        if not built_at:
            return None
        try:
            built = datetime.fromisoformat(built_at)
        except ValueError:
            return None
        if built.tzinfo is None:
            built = built.replace(tzinfo=timezone.utc)
        if (_utcnow() - built) > timedelta(days=ttl_days):
            return None
        return data

    def save_profile(self, profile: dict[str, Any]) -> None:
        profile = dict(profile)
        profile.setdefault("built_at", _utcnow().isoformat(timespec="seconds"))
        atomic_write_text(self.profile_path, json.dumps(profile, ensure_ascii=False, indent=2))

    def load_narrative(self, ttl_days: int) -> str | None:
        if not self.narrative_path.exists():
            return None
        mtime = datetime.fromtimestamp(self.narrative_path.stat().st_mtime, tz=timezone.utc)
        if (_utcnow() - mtime) > timedelta(days=ttl_days):
            return None
        return self.narrative_path.read_text(encoding="utf-8")

    def save_narrative(self, md: str) -> None:
        atomic_write_text(self.narrative_path, md)

    def append_run(self, entry: dict[str, Any]) -> None:
        append_jsonl_record(self.run_log_path, entry)

    def append_runtime_event(self, entry: dict[str, Any]) -> None:
        append_jsonl_record(self.runtime_events_path, entry)

    def append_ab_log(self, entry: dict[str, Any]) -> None:
        append_jsonl_record(self.ab_log_path, entry)

    def load_weekly_seen(self) -> dict[str, str]:
        if not self.weekly_seen_path.exists():
            return {}
        try:
            return json.loads(self.weekly_seen_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def is_recently_weekly_seen(self, paper_id: str, cooldown_days: int) -> bool:
        ts = self.load_weekly_seen().get(paper_id)
        if not ts:
            return False
        seen_day = _parse_day(ts)
        return bool(seen_day and (date.today() - seen_day) < timedelta(days=cooldown_days))

    def record_weekly_seen(self, paper_ids: list[str]) -> None:
        seen = self.load_weekly_seen()
        today = date.today().isoformat()
        for pid in paper_ids:
            seen[pid] = today
        atomic_write_text(self.weekly_seen_path, json.dumps(seen, ensure_ascii=False, indent=2))

    def gc_weekly_seen(self, cooldown_days: int) -> None:
        seen = self.load_weekly_seen()
        cutoff = date.today() - timedelta(days=cooldown_days * 2)
        pruned = {k: v for k, v in seen.items() if _parse_day(v) and _parse_day(v) >= cutoff}
        atomic_write_text(self.weekly_seen_path, json.dumps(pruned, ensure_ascii=False, indent=2))

    def append_weekly_report(self, entry: dict[str, Any]) -> None:
        append_jsonl_record(self.weekly_report_log_path, entry)

    # ───── Deep-seen (cluster-level) tracking ──────────────────────────
    # Separate from paper-level ``seen.json``. Used by the daily-research
    # pipeline so the same topic isn't deep-researched two days in a row.

    @property
    def deep_seen_path(self) -> Path:
        return self.root / "deep_seen.json"

    def load_deep_seen(self) -> dict[str, str]:
        if not self.deep_seen_path.exists():
            return {}
        try:
            return json.loads(self.deep_seen_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def is_recently_deep_seen(self, cluster_key: str, cooldown_days: int) -> bool:
        ts = self.load_deep_seen().get(cluster_key)
        if not ts:
            return False
        seen_day = _parse_day(ts)
        return bool(seen_day and (date.today() - seen_day) < timedelta(days=cooldown_days))

    def record_deep_seen(self, cluster_keys: list[str]) -> None:
        if not cluster_keys:
            return
        seen = self.load_deep_seen()
        today = date.today().isoformat()
        for k in cluster_keys:
            if k:
                seen[k] = today
        atomic_write_text(self.deep_seen_path, json.dumps(seen, ensure_ascii=False, indent=2))

    def gc_deep_seen(self, cooldown_days: int) -> None:
        seen = self.load_deep_seen()
        cutoff = date.today() - timedelta(days=cooldown_days * 2)
        pruned = {k: v for k, v in seen.items() if _parse_day(v) and _parse_day(v) >= cutoff}
        atomic_write_text(self.deep_seen_path, json.dumps(pruned, ensure_ascii=False, indent=2))

    def last_weekly_report_at(self) -> str | None:
        if not self.weekly_report_log_path.exists():
            return None
        last: str | None = None
        try:
            for line in self.weekly_report_log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("dry_run"):
                    continue
                run_at = entry.get("run_at")
                if isinstance(run_at, str):
                    last = run_at
        except OSError:
            return None
        return last


def _parse_day(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None
