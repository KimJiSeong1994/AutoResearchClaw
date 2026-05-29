#!/usr/bin/env python3
"""Persistent PaperWiki knowledge graph CLI.

Local-first SQLite KG for Obsidian/PaperWiki markdown vaults. Source notes are
read-only; the database is a replayable operational index with provenance,
events, freshness checks, and trust-aware query gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
EXTRACTOR_VERSION = "paperwiki-kg-1.0"
TRUST_POLICY_VERSION = "trust-policy-1.0"
DEFAULT_DB = ".omx/reports/paperwiki-kg/persistent/paperwiki_kg.sqlite"
SKIP_PARTS = {".git", "node_modules", "__pycache__"}
SKIP_PREFIXES = (".omx/state/", ".omc/state/")
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
INLINE_TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_/-]+)")
TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣_./+-]*")
EXIT_STALE = 2
EXIT_INVALID = 3
EXIT_TRUST = 4
EXIT_MISSING = 5


@dataclass(frozen=True)
class Link:
    raw: str
    target: str
    alias: str | None
    anchor: str | None


@dataclass
class SourceDoc:
    path: str
    abs_path: Path
    tier: str
    title: str
    frontmatter: dict[str, Any]
    body: str
    tags: list[str]
    aliases: list[str]
    headings: list[tuple[int, str, int]]
    links: list[Link]
    content_hash: str
    size: int
    mtime_ns: int
    trust_tier: str
    excluded_reason: str | None


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def should_skip(path: Path, root: Path) -> bool:
    r = rel(path, root)
    if path.name.endswith(".icloud"):
        return True
    if any(part in SKIP_PARTS for part in path.parts):
        return True
    return any(r.startswith(prefix) for prefix in SKIP_PREFIXES)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, Any] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if (line.startswith("  - ") or line.startswith("- ")) and current_key:
            data.setdefault(current_key, [])
            if not isinstance(data[current_key], list):
                data[current_key] = [data[current_key]]
            data[current_key].append(line.split("- ", 1)[1].strip().strip('"\''))
            continue
        current_key = None
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        current_key = key
        if val == "":
            data[key] = []
        elif val.startswith("[") and val.endswith("]"):
            data[key] = [x.strip().strip('"\'') for x in val[1:-1].split(",") if x.strip()]
        else:
            data[key] = val.strip('"\'')
    return data, body


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            return [x.strip().strip('"\'') for x in s[1:-1].split(",") if x.strip()]
        return [s] if s else []
    return [str(value)]


def parse_link(raw: str) -> Link:
    target_part, alias = (raw.split("|", 1) + [None])[:2] if "|" in raw else (raw, None)
    if "#" in target_part:
        target, anchor = target_part.split("#", 1)
    else:
        target, anchor = target_part, None
    return Link(raw=raw.strip(), target=target.strip(), alias=alias.strip() if alias else None, anchor=anchor.strip() if anchor else None)


def extract_headings(body: str) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    for idx, line in enumerate(body.splitlines(), 1):
        if line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            if 1 <= hashes <= 6 and line[hashes:hashes+1] == " ":
                out.append((hashes, line[hashes:].strip(), idx))
    return out


def classify_path(path: Path, root: Path) -> str:
    r = rel(path, root)
    if r.startswith("pages/"):
        return "pages"
    if r.startswith("raw/"):
        return "raw"
    if r.startswith(".omx/reports/"):
        return "reports"
    return "other"


def classify_trust(tier: str, fm: dict[str, Any], path: str) -> tuple[str, str | None]:
    trust_status = str(fm.get("trust_status") or "").lower()
    quarantine = str(fm.get("quarantine") or "").lower()
    generated_by = str(fm.get("generated_by") or "").lower()
    if trust_status == "unreviewed-generated" or quarantine == "true" or generated_by:
        return "generated-unreviewed", "generated_or_quarantined"
    if tier == "raw":
        return "raw", "raw_opt_in_required"
    if tier == "reports":
        return "report", "reports_opt_in_required"
    if tier == "pages":
        return "trusted", None
    return "other", "outside_default_scope"


def read_source(path: Path, root: Path) -> SourceDoc | None:
    if should_skip(path, root):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    text = data.decode("utf-8", errors="ignore")
    fm, body = parse_frontmatter(text)
    tier = classify_path(path, root)
    r = rel(path, root)
    title = str(fm.get("title") or path.stem)
    tags = sorted(set(as_list(fm.get("tags")) + [m.group(1) for m in INLINE_TAG_RE.finditer(body)]))
    aliases = as_list(fm.get("aliases"))
    headings = extract_headings(body)
    links = [parse_link(x) for x in WIKILINK_RE.findall(body)]
    stat = path.stat()
    trust_tier, excluded_reason = classify_trust(tier, fm, r)
    return SourceDoc(
        path=r, abs_path=path, tier=tier, title=title, frontmatter=fm, body=body,
        tags=tags, aliases=aliases, headings=headings, links=links,
        content_hash=sha256_bytes(data), size=stat.st_size, mtime_ns=stat.st_mtime_ns,
        trust_tier=trust_tier, excluded_reason=excluded_reason,
    )


def iter_markdown(root: Path, include_raw: bool, include_reports: bool) -> Iterable[Path]:
    bases = [root / "pages"]
    if include_raw:
        bases.append(root / "raw")
    if include_reports:
        bases.append(root / ".omx" / "reports")
    for base in bases:
        if base.exists():
            yield from sorted(base.rglob("*.md"))
    for path in sorted(root.glob("*.md")):
        yield path


def load_sources(root: Path, include_raw: bool, include_reports: bool) -> tuple[list[SourceDoc], list[dict[str, Any]]]:
    docs: list[SourceDoc] = []
    diagnostics: list[dict[str, Any]] = []
    for placeholder in root.rglob("*.icloud") if root.exists() else []:
        diagnostics.append({"code": "icloud_placeholder", "path": rel(placeholder, root), "severity": "warning"})
    for path in iter_markdown(root, include_raw, include_reports):
        doc = read_source(path, root)
        if doc is None:
            diagnostics.append({"code": "skipped_source", "path": rel(path, root), "severity": "info"})
        else:
            docs.append(doc)
    return docs, diagnostics


def connect(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(f"""
    CREATE TABLE IF NOT EXISTS kg_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS kg_sources(
      source_id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, tier TEXT NOT NULL,
      title TEXT NOT NULL, content_hash TEXT NOT NULL, size INTEGER NOT NULL,
      mtime_ns INTEGER NOT NULL, trust_tier TEXT NOT NULL, excluded_reason TEXT,
      frontmatter_json TEXT NOT NULL, tombstone INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kg_nodes(
      node_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
      title TEXT NOT NULL, node_type TEXT NOT NULL, trust_tier TEXT NOT NULL,
      tombstone INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(source_id) REFERENCES kg_sources(source_id)
    );
    CREATE TABLE IF NOT EXISTS kg_edges(
      edge_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
      target_text TEXT NOT NULL, target_node_id TEXT, raw_target TEXT NOT NULL,
      alias TEXT, anchor TEXT, resolution_state TEXT NOT NULL, candidates_json TEXT NOT NULL,
      trust_tier TEXT NOT NULL, tombstone INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(source_id) REFERENCES kg_sources(source_id)
    );
    CREATE TABLE IF NOT EXISTS kg_chunks(
      chunk_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, path TEXT NOT NULL,
      title TEXT NOT NULL, text TEXT NOT NULL, line_start INTEGER NOT NULL,
      line_end INTEGER NOT NULL, trust_tier TEXT NOT NULL, source_hash TEXT NOT NULL,
      tombstone INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(source_id) REFERENCES kg_sources(source_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(chunk_id UNINDEXED, title, path, text);
    CREATE TABLE IF NOT EXISTS kg_assertions(
      assertion_id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
      assertion TEXT NOT NULL, trust_tier TEXT NOT NULL, source_id TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kg_events(
      event_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, event_type TEXT NOT NULL,
      source_path TEXT, source_hash_before TEXT, source_hash_after TEXT,
      extractor_version TEXT NOT NULL, schema_version TEXT NOT NULL,
      trust_policy_version TEXT NOT NULL, observed_at TEXT NOT NULL, applied_at TEXT NOT NULL,
      event_digest TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kg_provenance(
      provenance_id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
      source_path TEXT NOT NULL, source_hash TEXT NOT NULL, line_start INTEGER,
      line_end INTEGER, extractor_version TEXT NOT NULL, trust_policy_version TEXT NOT NULL,
      ingest_event_id TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kg_diagnostics(
      diagnostic_id TEXT PRIMARY KEY, code TEXT NOT NULL, path TEXT, severity TEXT NOT NULL,
      message TEXT, data_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS kg_health(
      key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    """)
    for key, value in {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "trust_policy_version": TRUST_POLICY_VERSION,
    }.items():
        con.execute("INSERT OR REPLACE INTO kg_meta(key,value) VALUES(?,?)", (key, value))


def clear_graph(con: sqlite3.Connection) -> None:
    for table in ["fts_chunks", "kg_provenance", "kg_assertions", "kg_edges", "kg_chunks", "kg_nodes", "kg_sources", "kg_diagnostics", "kg_events", "kg_health"]:
        con.execute(f"DELETE FROM {table}")


def build_resolver(docs: list[SourceDoc]) -> dict[str, list[str]]:
    resolver: dict[str, list[str]] = {}
    for d in docs:
        keys = {Path(d.path).stem.lower(), d.title.lower(), d.path.lower()}
        keys.update(a.lower() for a in d.aliases)
        for k in keys:
            resolver.setdefault(k, []).append(d.path)
    return {k: sorted(set(v)) for k, v in resolver.items()}


def source_manifest(docs: list[SourceDoc], tombstones: list[sqlite3.Row] | None = None) -> list[dict[str, Any]]:
    rows = [{"path": d.path, "hash": d.content_hash, "tier": d.tier, "trust_tier": d.trust_tier, "tombstone": False} for d in docs]
    for row in tombstones or []:
        rows.append({"path": row["path"], "hash": row["content_hash"], "tier": row["tier"], "trust_tier": row["trust_tier"], "tombstone": True})
    return sorted(rows, key=lambda x: x["path"])


def fingerprint_from_manifest(manifest: list[dict[str, Any]], event_tail: str = "") -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "trust_policy_version": TRUST_POLICY_VERSION,
        "sources": manifest,
        "event_tail_digest": event_tail,
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def event_digest(event: dict[str, Any]) -> str:
    e = {k: v for k, v in event.items() if k != "event_digest"}
    return sha256_text(json.dumps(e, sort_keys=True, ensure_ascii=False))


def insert_event(con: sqlite3.Connection, batch_id: str, event_type: str, source_path: str | None = None,
                 before: str | None = None, after: str | None = None) -> str:
    event_id = str(uuid.uuid4())
    t = now()
    event = {
        "event_id": event_id, "batch_id": batch_id, "event_type": event_type,
        "source_path": source_path, "source_hash_before": before, "source_hash_after": after,
        "extractor_version": EXTRACTOR_VERSION, "schema_version": SCHEMA_VERSION,
        "trust_policy_version": TRUST_POLICY_VERSION, "observed_at": t, "applied_at": t,
    }
    digest = event_digest(event)
    con.execute("""INSERT INTO kg_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
        event_id, batch_id, event_type, source_path, before, after, EXTRACTOR_VERSION,
        SCHEMA_VERSION, TRUST_POLICY_VERSION, t, t, digest,
    ))
    return event_id


def insert_diag(con: sqlite3.Connection, code: str, path: str | None, severity: str, message: str = "", data: Any = None) -> None:
    con.execute("INSERT INTO kg_diagnostics VALUES(?,?,?,?,?,?,?)", (
        stable_id("diag", code, path or "", str(time.time_ns())), code, path, severity,
        message, json.dumps(data or {}, ensure_ascii=False, sort_keys=True), now(),
    ))


def recompute_event_tail_digest(con: sqlite3.Connection) -> str:
    rows = con.execute("SELECT * FROM kg_events ORDER BY applied_at, event_id").fetchall()
    return sha256_text("".join(event_digest(dict(row)) for row in rows))


def compute_graph_digest(con: sqlite3.Connection) -> str:
    payload: dict[str, list[dict[str, Any]]] = {}
    for table in ["kg_sources", "kg_nodes", "kg_edges", "kg_chunks", "kg_assertions", "kg_provenance"]:
        rows = [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY 1")]
        payload[table] = rows
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def compute_fts_digest(con: sqlite3.Connection) -> str:
    rows = [dict(r) for r in con.execute("SELECT chunk_id, title, path, text FROM fts_chunks ORDER BY chunk_id")]
    return sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True))


def validate_fts_consistency(con: sqlite3.Connection) -> tuple[bool, str | None]:
    chunk_rows = [dict(r) for r in con.execute(
        "SELECT chunk_id, title, path, text FROM kg_chunks WHERE tombstone=0 ORDER BY chunk_id"
    )]
    fts_rows = [dict(r) for r in con.execute("SELECT chunk_id, title, path, text FROM fts_chunks ORDER BY chunk_id")]
    if chunk_rows != fts_rows:
        return False, "fts_chunks_mismatch"
    return True, None


def chunk_body(doc: SourceDoc) -> list[tuple[str, int, int]]:
    lines = doc.body.splitlines()
    if not lines:
        return [("", 1, 1)]
    chunks: list[tuple[str, int, int]] = []
    start = 1
    buf: list[str] = []
    for idx, line in enumerate(lines, 1):
        if line.startswith("# ") and buf:
            chunks.append(("\n".join(buf).strip(), start, idx - 1))
            start = idx
            buf = [line]
        else:
            buf.append(line)
    if buf:
        chunks.append(("\n".join(buf).strip(), start, len(lines)))
    return [c for c in chunks if c[0]] or [(doc.body.strip(), 1, max(1, len(lines)))]


def upsert_doc(con: sqlite3.Connection, doc: SourceDoc, resolver: dict[str, list[str]], batch_id: str, full_build: bool) -> None:
    source_id = stable_id("source", doc.path)
    node_id = stable_id("node", doc.path)
    old = con.execute("SELECT content_hash FROM kg_sources WHERE path=?", (doc.path,)).fetchone()
    event_type = "source_added" if old is None else ("source_modified" if old["content_hash"] != doc.content_hash else "sync")
    event_id = insert_event(con, batch_id, event_type if not full_build else "build", doc.path, old["content_hash"] if old else None, doc.content_hash)
    con.execute("""INSERT OR REPLACE INTO kg_sources VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
        source_id, doc.path, doc.tier, doc.title, doc.content_hash, doc.size, doc.mtime_ns,
        doc.trust_tier, doc.excluded_reason, json.dumps(doc.frontmatter, ensure_ascii=False, sort_keys=True), 0, now(),
    ))
    old_object_ids = [node_id]
    old_object_ids.extend(r["edge_id"] for r in con.execute("SELECT edge_id FROM kg_edges WHERE source_id=?", (source_id,)).fetchall())
    old_object_ids.extend(r["chunk_id"] for r in con.execute("SELECT chunk_id FROM kg_chunks WHERE source_id=?", (source_id,)).fetchall())
    old_object_ids.extend(r["assertion_id"] for r in con.execute("SELECT assertion_id FROM kg_assertions WHERE source_id=?", (source_id,)).fetchall())
    for object_id in old_object_ids:
        con.execute("DELETE FROM kg_provenance WHERE object_id=?", (object_id,))
    con.execute("DELETE FROM kg_assertions WHERE source_id=?", (source_id,))
    con.execute("DELETE FROM kg_nodes WHERE source_id=?", (source_id,))
    con.execute("DELETE FROM kg_edges WHERE source_id=?", (source_id,))
    old_chunk_ids = [r["chunk_id"] for r in con.execute("SELECT chunk_id FROM kg_chunks WHERE source_id=?", (source_id,)).fetchall()]
    for old_chunk_id in old_chunk_ids:
        con.execute("DELETE FROM fts_chunks WHERE chunk_id=?", (old_chunk_id,))
    con.execute("DELETE FROM kg_chunks WHERE source_id=?", (source_id,))
    con.execute("INSERT OR REPLACE INTO kg_nodes VALUES(?,?,?,?,?,?,?)", (node_id, source_id, doc.path, doc.title, "note", doc.trust_tier, 0))
    con.execute("INSERT OR REPLACE INTO kg_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
        stable_id("prov", "node", node_id), "node", node_id, doc.path, doc.content_hash, None, None,
        EXTRACTOR_VERSION, TRUST_POLICY_VERSION, event_id, now(),
    ))
    for tag in doc.tags:
        assertion_id = stable_id("assertion", doc.path, "tag", tag)
        con.execute("INSERT OR REPLACE INTO kg_assertions VALUES(?,?,?,?,?,?)", (
            assertion_id, "node", node_id, f"tag:{tag}", doc.trust_tier, source_id,
        ))
        con.execute("INSERT OR REPLACE INTO kg_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            stable_id("prov", "assertion", assertion_id), "assertion", assertion_id,
            doc.path, doc.content_hash, None, None, EXTRACTOR_VERSION,
            TRUST_POLICY_VERSION, event_id, now(),
        ))
    for text, line_start, line_end in chunk_body(doc):
        chunk_id = stable_id("chunk", doc.path, str(line_start), sha256_text(text)[:12])
        con.execute("INSERT OR REPLACE INTO kg_chunks VALUES(?,?,?,?,?,?,?,?,?,?)", (chunk_id, source_id, doc.path, doc.title, text, line_start, line_end, doc.trust_tier, doc.content_hash, 0))
        con.execute("INSERT INTO fts_chunks(chunk_id,title,path,text) VALUES(?,?,?,?)", (chunk_id, doc.title, doc.path, text))
        con.execute("INSERT OR REPLACE INTO kg_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            stable_id("prov", "chunk", chunk_id), "chunk", chunk_id, doc.path, doc.content_hash, line_start, line_end,
            EXTRACTOR_VERSION, TRUST_POLICY_VERSION, event_id, now(),
        ))
    for link in doc.links:
        candidates = resolver.get(link.target.lower(), [])
        target_node_id = None
        state = "unresolved"
        if len(candidates) == 1:
            state = "resolved"
            target_node_id = stable_id("node", candidates[0])
        elif len(candidates) > 1:
            state = "ambiguous"
        trust = doc.trust_tier if doc.trust_tier == "trusted" and state == "resolved" else "untrusted"
        edge_id = stable_id("edge", doc.path, link.raw)
        con.execute("INSERT OR REPLACE INTO kg_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            edge_id, source_id, node_id, link.target, target_node_id, link.raw, link.alias, link.anchor,
            state, json.dumps(candidates, ensure_ascii=False), trust, 0,
        ))
        con.execute("INSERT OR REPLACE INTO kg_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            stable_id("prov", "edge", edge_id), "edge", edge_id, doc.path, doc.content_hash, None, None,
            EXTRACTOR_VERSION, TRUST_POLICY_VERSION, event_id, now(),
        ))
        if state != "resolved":
            insert_diag(con, f"wikilink_{state}", doc.path, "warning", f"{link.raw} is {state}", {"candidates": candidates})


def update_health(con: sqlite3.Connection, vault: Path | None, docs: list[SourceDoc], include_raw: bool = False, include_reports: bool = False) -> dict[str, Any]:
    tombstones = con.execute("SELECT * FROM kg_sources WHERE tombstone=1").fetchall()
    event_tail_digest = recompute_event_tail_digest(con)
    manifest = source_manifest(docs, tombstones)
    fp = fingerprint_from_manifest(manifest, event_tail_digest)
    counts = {
        "sources": con.execute("SELECT COUNT(*) c FROM kg_sources WHERE tombstone=0").fetchone()["c"],
        "nodes": con.execute("SELECT COUNT(*) c FROM kg_nodes WHERE tombstone=0").fetchone()["c"],
        "edges": con.execute("SELECT COUNT(*) c FROM kg_edges WHERE tombstone=0").fetchone()["c"],
        "chunks": con.execute("SELECT COUNT(*) c FROM kg_chunks WHERE tombstone=0").fetchone()["c"],
        "diagnostics": con.execute("SELECT COUNT(*) c FROM kg_diagnostics").fetchone()["c"],
        "tombstones": con.execute("SELECT COUNT(*) c FROM kg_sources WHERE tombstone=1").fetchone()["c"],
    }
    health = {
        "schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION,
        "trust_policy_version": TRUST_POLICY_VERSION, "vault": str(vault) if vault else None,
        "source_fingerprint": fp, "event_tail_digest": event_tail_digest,
        "graph_digest": compute_graph_digest(con),
        "fts_digest": compute_fts_digest(con),
        "counts": counts, "updated_at": now(), "integrity": "ok",
        "source_scope": {"include_raw": include_raw, "include_reports": include_reports},
    }
    for k, v in health.items():
        con.execute("INSERT OR REPLACE INTO kg_health VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False, sort_keys=True)))
    return health


def read_health(con: sqlite3.Connection) -> dict[str, Any]:
    rows = con.execute("SELECT key,value FROM kg_health").fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}


def validate_db(db: Path, vault: Path | None = None) -> tuple[int, dict[str, Any]]:
    if not db.exists():
        return EXIT_MISSING, {"ok": False, "fresh": False, "error": "missing_db", "recommended_action": "build"}
    try:
        con = connect(db)
        init_schema(con)
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        health = read_health(con)
        required = {"schema_version": SCHEMA_VERSION, "extractor_version": EXTRACTOR_VERSION, "trust_policy_version": TRUST_POLICY_VERSION}
        for k, expected in required.items():
            if health.get(k) != expected:
                return EXIT_INVALID, {"ok": False, "fresh": False, "error": f"{k}_mismatch", "recommended_action": "repair", "health": health}
        if integrity.lower() != "ok":
            return EXIT_INVALID, {"ok": False, "fresh": False, "error": "integrity_failure", "recommended_action": "repair", "integrity": integrity}
        # FTS presence smoke.
        con.execute("SELECT COUNT(*) FROM fts_chunks").fetchone()
        fts_ok, fts_error = validate_fts_consistency(con)
        if not fts_ok:
            return EXIT_INVALID, {
                "ok": False, "fresh": False, "error": fts_error,
                "recommended_action": "repair", "health": health,
            }
        recomputed_fts_digest = compute_fts_digest(con)
        if recomputed_fts_digest != health.get("fts_digest"):
            return EXIT_INVALID, {
                "ok": False, "fresh": False, "error": "fts_digest_mismatch",
                "recommended_action": "repair", "health": health,
                "recomputed_fts_digest": recomputed_fts_digest,
            }
        recomputed_event_tail = recompute_event_tail_digest(con)
        if recomputed_event_tail != health.get("event_tail_digest"):
            return EXIT_INVALID, {
                "ok": False, "fresh": False, "error": "event_tail_mismatch",
                "recommended_action": "repair", "health": health,
                "recomputed_event_tail_digest": recomputed_event_tail,
            }
        recomputed_graph_digest = compute_graph_digest(con)
        if recomputed_graph_digest != health.get("graph_digest"):
            return EXIT_INVALID, {
                "ok": False, "fresh": False, "error": "graph_digest_mismatch",
                "recommended_action": "repair", "health": health,
                "recomputed_graph_digest": recomputed_graph_digest,
            }
        if vault is not None and vault.exists():
            scope = health.get("source_scope", {}) or {}
            docs, _ = load_sources(vault, include_raw=bool(scope.get("include_raw")), include_reports=bool(scope.get("include_reports")))
            tombstones = con.execute("SELECT * FROM kg_sources WHERE tombstone=1").fetchall()
            live_fp = fingerprint_from_manifest(source_manifest(docs, tombstones), recomputed_event_tail)
            if live_fp != health.get("source_fingerprint"):
                return EXIT_STALE, {"ok": False, "fresh": False, "error": "source_fingerprint_drift", "recommended_action": "sync", "health": health, "live_source_fingerprint": live_fp}
        return 0, {"ok": True, "fresh": True, "recommended_action": "none", "health": health}
    except sqlite3.Error as e:
        return EXIT_INVALID, {"ok": False, "fresh": False, "error": "sqlite_error", "message": str(e), "recommended_action": "repair"}


def cmd_build(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser().resolve()
    db = Path(args.db).expanduser()
    docs, diagnostics = load_sources(vault, args.include_raw, args.include_reports)
    con = connect(db)
    with con:
        init_schema(con)
        clear_graph(con)
        batch = str(uuid.uuid4())
        resolver = build_resolver(docs)
        for d in docs:
            upsert_doc(con, d, resolver, batch, full_build=True)
        for diag in diagnostics:
            insert_diag(con, diag["code"], diag.get("path"), diag.get("severity", "warning"), diag.get("message", ""), diag)
        health = update_health(con, vault, docs, args.include_raw, args.include_reports)
        con.execute("INSERT OR REPLACE INTO kg_health VALUES(?,?)", ("last_build_at", json.dumps(now())))
    out = {"ok": True, "command": "build", "db_path": str(db), "vault": str(vault), **health}
    print_json_or_text(out, args.json)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser().resolve()
    db = Path(args.db).expanduser()
    docs, diagnostics = load_sources(vault, args.include_raw, args.include_reports)
    con = connect(db)
    changed = 0
    tombstoned = 0
    with con:
        init_schema(con)
        batch = str(uuid.uuid4())
        resolver = build_resolver(docs)
        live_paths = {d.path for d in docs}
        old_rows = {r["path"]: r for r in con.execute("SELECT * FROM kg_sources WHERE tombstone=0").fetchall()}
        changed_docs = []
        for d in docs:
            old = old_rows.get(d.path)
            if old is None or old["content_hash"] != d.content_hash:
                changed += 1
                changed_docs.append(d)
        for path, old in old_rows.items():
            if path not in live_paths:
                tombstoned += 1
                insert_event(con, batch, "source_deleted", path, old["content_hash"], None)
                source_id = old["source_id"]
                con.execute("UPDATE kg_sources SET tombstone=1, updated_at=? WHERE source_id=?", (now(), source_id))
                con.execute("UPDATE kg_nodes SET tombstone=1 WHERE source_id=?", (source_id,))
                con.execute("UPDATE kg_edges SET tombstone=1 WHERE source_id=?", (source_id,))
                for old_chunk in con.execute("SELECT chunk_id FROM kg_chunks WHERE source_id=?", (source_id,)).fetchall():
                    con.execute("DELETE FROM fts_chunks WHERE chunk_id=?", (old_chunk["chunk_id"],))
                con.execute("UPDATE kg_chunks SET tombstone=1 WHERE source_id=?", (source_id,))
        docs_to_upsert = docs if (changed or tombstoned) else changed_docs
        for d in docs_to_upsert:
            upsert_doc(con, d, resolver, batch, full_build=False)
        for diag in diagnostics:
            insert_diag(con, diag["code"], diag.get("path"), diag.get("severity", "warning"), diag.get("message", ""), diag)
        health = update_health(con, vault, docs, args.include_raw, args.include_reports)
        con.execute("INSERT OR REPLACE INTO kg_health VALUES(?,?)", ("last_sync_at", json.dumps(now())))
    out = {"ok": True, "command": "sync", "changed": changed, "tombstoned": tombstoned, "db_path": str(db), "vault": str(vault), **health}
    print_json_or_text(out, args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser()
    vault = Path(args.vault).expanduser().resolve() if args.vault else None
    if vault is None and db.exists():
        try:
            hv = read_health(connect(db)).get("vault")
            if hv:
                vault = Path(hv).expanduser().resolve()
        except sqlite3.Error:
            pass
    code, status = validate_db(db, vault)
    health = status.get("health", {})
    out = {
        "ok": status.get("ok", False), "fresh": status.get("fresh", False),
        "db_path": str(db), "vault": str(vault) if vault else health.get("vault"),
        "schema_version": health.get("schema_version"), "extractor_version": health.get("extractor_version"),
        "trust_policy_version": health.get("trust_policy_version"), "source_fingerprint": health.get("source_fingerprint"),
        "event_tail_digest": health.get("event_tail_digest"), "counts": health.get("counts", {}),
        "diagnostics": status.get("diagnostics", {}), "last_build_at": health.get("last_build_at"),
        "last_sync_at": health.get("last_sync_at"), "recommended_action": status.get("recommended_action", "none"),
    }
    if "error" in status:
        out["error"] = status["error"]
    print_json_or_text(out, args.json)
    return code if args.strict else 0


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text)]


def sql_match_query(query: str) -> str:
    toks = tokenize(query)
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in toks) if toks else '"' + query.replace('"', '""') + '"'


def cmd_query(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser()
    vault = Path(args.vault).expanduser().resolve() if args.vault else None
    if vault is None and db.exists():
        try:
            hv = read_health(connect(db)).get("vault")
            if hv:
                vault = Path(hv).expanduser().resolve()
        except sqlite3.Error:
            pass
    code, status = validate_db(db, vault)
    if args.strict and code != 0:
        out = {"ok": False, "fresh": False, "query": args.query, "db_path": str(db), "trust_policy": TRUST_POLICY_VERSION, "results": [], "diagnostics": [status]}
        print_json_or_markdown(out, args.format)
        return code
    if code == EXIT_MISSING:
        print_json_or_markdown({"ok": False, "fresh": False, "error": "missing_db", "results": []}, args.format)
        return code
    con = connect(db)
    def row_allowed(row: sqlite3.Row) -> bool:
        trust = row["trust_tier"]
        if trust == "trusted":
            return True
        if trust == "raw" and args.include_raw:
            return True
        if trust == "report" and args.include_reports:
            return True
        if args.include_untrusted:
            return True
        return False

    def result_from_chunk(row: sqlite3.Row, *, rank: float, reason_prefix: str = "fts") -> dict[str, Any]:
        edge_rows = con.execute("SELECT raw_target,resolution_state,target_text FROM kg_edges WHERE source_id=? AND tombstone=0 LIMIT 8", (row["source_id"],)).fetchall()
        return {
            "path": row["path"], "title": row["title"], "chunk_id": row["chunk_id"],
            "line_start": row["line_start"], "line_end": row["line_end"],
            "score": rank,
            "trust_tier": row["trust_tier"], "source_hash": row["source_hash"],
            "edge_reasons": [f"{reason_prefix}:{e['resolution_state']}:{e['raw_target']}" for e in edge_rows],
            "citations": [{"path": row["path"], "line_start": row["line_start"], "line_end": row["line_end"]}],
            "warnings": [] if row["trust_tier"] == "trusted" else ["untrusted_or_opt_in_source"],
            "excerpt": row["text"][:500],
        }

    match = sql_match_query(args.query)
    try:
        rows = con.execute("""
            SELECT c.*, bm25(fts_chunks) AS rank
            FROM fts_chunks JOIN kg_chunks c ON fts_chunks.chunk_id = c.chunk_id
            WHERE fts_chunks MATCH ? AND c.tombstone=0
            ORDER BY rank LIMIT ?
        """, (match, args.limit * 3)).fetchall()
    except sqlite3.OperationalError as e:
        out = {
            "ok": False, "fresh": False, "query": args.query, "db_path": str(db),
            "trust_policy": TRUST_POLICY_VERSION, "results": [],
            "diagnostics": [{"error": "fts_query_error", "message": str(e), "recommended_action": "repair"}],
        }
        print_json_or_markdown(out, args.format)
        return EXIT_INVALID
    results = []
    seen_chunks: set[str] = set()
    seed_source_ids: list[str] = []
    for row in rows:
        if not row_allowed(row):
            continue
        seen_chunks.add(row["chunk_id"])
        seed_source_ids.append(row["source_id"])
        results.append(result_from_chunk(row, rank=float(-row["rank"] if row["rank"] is not None else 0.0)))
        if len(results) >= args.limit:
            break
    # Trust-aware one-hop graph expansion from FTS seeds. Only resolved trusted
    # source->edge->target paths are traversed unless broad untrusted inclusion is
    # explicitly requested.
    if len(results) < args.limit:
        for source_id in seed_source_ids:
            edge_rows = con.execute("""
                SELECT e.target_node_id, e.raw_target
                FROM kg_edges e
                JOIN kg_nodes src ON src.node_id = e.source_node_id
                JOIN kg_nodes tgt ON tgt.node_id = e.target_node_id
                WHERE e.source_id=? AND e.tombstone=0 AND e.resolution_state='resolved'
                  AND (e.trust_tier='trusted' OR ?)
                  AND (src.trust_tier='trusted' OR ?)
                  AND (tgt.trust_tier='trusted' OR ?)
            """, (source_id, int(args.include_untrusted), int(args.include_untrusted), int(args.include_untrusted))).fetchall()
            for edge in edge_rows:
                chunks = con.execute("""
                    SELECT c.*, 0.0 AS rank
                    FROM kg_chunks c JOIN kg_nodes n ON n.source_id = c.source_id
                    WHERE n.node_id=? AND c.tombstone=0
                    ORDER BY c.line_start LIMIT 1
                """, (edge["target_node_id"],)).fetchall()
                for chunk in chunks:
                    if chunk["chunk_id"] in seen_chunks or not row_allowed(chunk):
                        continue
                    seen_chunks.add(chunk["chunk_id"])
                    results.append(result_from_chunk(chunk, rank=0.05, reason_prefix=f"graph-hop:{edge['raw_target']}"))
                    if len(results) >= args.limit:
                        break
                if len(results) >= args.limit:
                    break
            if len(results) >= args.limit:
                break
    out = {"ok": True, "fresh": status.get("fresh", False), "query": args.query, "db_path": str(db), "trust_policy": TRUST_POLICY_VERSION, "results": results, "diagnostics": [] if code == 0 else [status]}
    print_json_or_markdown(out, args.format)
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    db = Path(args.db).expanduser()
    outdir = Path(args.out).expanduser()
    code, status = validate_db(db, None)
    if code != 0:
        print_json_or_text({"ok": False, "error": status}, args.json)
        return code
    outdir.mkdir(parents=True, exist_ok=True)
    backup = outdir / "paperwiki_kg.sqlite"
    src = sqlite3.connect(db)
    dst = sqlite3.connect(backup)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    con = connect(db)
    manifest = {"created_at": now(), "db": str(backup), "health": read_health(con)}
    for table in ["kg_sources", "kg_nodes", "kg_edges", "kg_chunks", "kg_events", "kg_provenance"]:
        with (outdir / f"{table}.jsonl").open("w", encoding="utf-8") as f:
            for row in con.execute(f"SELECT * FROM {table}"):
                f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    (outdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print_json_or_text({"ok": True, "command": "checkpoint", "out": str(outdir), "manifest": str(outdir / "manifest.json")}, args.json)
    return 0


def print_json_or_text(out: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


def print_json_or_markdown(out: dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print("# PaperWiki KG Query\n")
    print(f"- query: `{out.get('query','')}`")
    print(f"- fresh: `{out.get('fresh')}`")
    if not out.get("ok"):
        print(f"- error: `{out.get('error', out.get('diagnostics'))}`")
        return
    for i, r in enumerate(out.get("results", []), 1):
        print(f"\n## {i}. {r['title']}")
        print(f"- path: `{r['path']}`")
        print(f"- lines: {r['line_start']}-{r['line_end']}")
        print(f"- trust: `{r['trust_tier']}`")
        if r.get("edge_reasons"):
            print("- edges: " + ", ".join(f"`{x}`" for x in r["edge_reasons"][:6]))
        print("\n" + r.get("excerpt", "").strip()[:500] + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Persistent PaperWiki KG")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", default=DEFAULT_DB)
        p.add_argument("--json", action="store_true")
    for name in ["build", "sync"]:
        p = sub.add_parser(name)
        p.add_argument("--vault", required=True)
        p.add_argument("--include-raw", action="store_true")
        p.add_argument("--include-reports", action="store_true")
        add_common(p)
    p = sub.add_parser("status")
    p.add_argument("--vault")
    p.add_argument("--strict", action="store_true")
    add_common(p)
    p = sub.add_parser("query")
    p.add_argument("--query", required=True)
    p.add_argument("--vault")
    p.add_argument("--format", choices=["json", "markdown"], default="markdown")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--include-raw", action="store_true")
    p.add_argument("--include-reports", action="store_true")
    p.add_argument("--include-untrusted", action="store_true")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--db", default=DEFAULT_DB)
    p = sub.add_parser("checkpoint")
    p.add_argument("--out", required=True)
    add_common(p)
    args = ap.parse_args(argv)
    return globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    raise SystemExit(main())
