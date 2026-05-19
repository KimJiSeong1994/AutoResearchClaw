from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


URL_RE = re.compile(r"https?://[^\s)\]}>'\"]+")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_RE = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9가-힣]+", re.IGNORECASE)
OVERCLAIM_RE = re.compile(
    r"\b(always|never|guarantee[sd]?|proves?|definitive(?:ly)?|revolutionary)\b",
    re.IGNORECASE,
)

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "code",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}
PRIVATE_HOSTS = {"localhost", "metadata.google.internal"}
NON_EVIDENCE_DOMAINS = {"discord.com", "cdn.discordapp.com", "media.discordapp.net"}
NON_EVIDENCE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


class PublicationTrustGateError(RuntimeError):
    """Raised when Editor/Advisor pre-publication review blocks publishing."""


@dataclass(frozen=True)
class PublicationTrustGateConfig:
    enabled: bool = True
    report_dir: Path = Path.home() / ".openclaw" / "workspace" / "reports" / "jiphyeonjeon-trust-gates"
    min_evidence: int = 2
    min_domains: int = 1
    block_on_advisor_non_pass: bool = True
    block_on_editor_duplicates: bool = True
    extra_identity_inputs: tuple[Path, ...] = ()


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise PublicationTrustGateError(f"invalid integer env var {name}: {raw}") from exc


def _env_paths(name: str) -> tuple[Path, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ()
    return tuple(Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip())


def trust_gate_config_from_env() -> PublicationTrustGateConfig:
    report_dir = Path(
        os.environ.get(
            "JIPHYEONJEON_TRUST_GATE_REPORT_DIR",
            str(Path.home() / ".openclaw" / "workspace" / "reports" / "jiphyeonjeon-trust-gates"),
        )
    ).expanduser()
    return PublicationTrustGateConfig(
        enabled=_env_flag("JIPHYEONJEON_PUBLICATION_TRUST_GATE", "1"),
        report_dir=report_dir,
        min_evidence=_env_int("JIPHYEONJEON_ADVISOR_MIN_EVIDENCE", 2),
        min_domains=_env_int("JIPHYEONJEON_ADVISOR_MIN_DOMAINS", 1),
        block_on_advisor_non_pass=_env_flag("JIPHYEONJEON_ADVISOR_BLOCK_NON_PASS", "1"),
        block_on_editor_duplicates=_env_flag("JIPHYEONJEON_EDITOR_BLOCK_DUPLICATES", "1"),
        extra_identity_inputs=_env_paths("JIPHYEONJEON_EDITOR_IDENTITY_INPUTS"),
    )


def _generated_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
        return rows
    value = json.loads(text)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("items", "candidates", "rows", "papers", "records", "sources", "evidence"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
        return [value]
    return []


def _read_identity_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() in {".json", ".jsonl"}:
        return _read_json_rows(path)
    text = path.read_text(encoding="utf-8")
    rows = []
    for index, url in enumerate(sorted(set(url.rstrip(".,") for url in URL_RE.findall(text)))):
        rows.append({"title": f"markdown-url-{index}", "url": url})
    return rows


def _first_str(row: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _is_private_host(host: str) -> bool:
    hostname = host.split(":", 1)[0].strip("[]").lower()
    if not hostname or hostname in PRIVATE_HOSTS or hostname.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _clean_url(raw: str) -> str:
    if not raw:
        return ""
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.username or parts.password or "@" in parts.netloc:
        return ""
    host = parts.netloc.lower()
    if _is_private_host(host):
        return ""
    path = re.sub(r"/+$", "", parts.path or "/") or "/"
    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=False):
        key_l = key.lower()
        if (
            key_l in SENSITIVE_QUERY_KEYS
            or key_l in TRACKING_QUERY_KEYS
            or any(key_l.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
        ):
            continue
        query_pairs.append((key, value))
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def _title_fingerprint(title: str) -> str:
    words = WORD_RE.findall(title.lower())
    collapsed = " ".join(words)
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:16] if collapsed else ""


def _canonical_key(row: dict[str, Any]) -> str:
    doi = _first_str(row, ("doi", "DOI"))
    if not doi:
        blob = " ".join(str(row.get(k) or "") for k in ("url", "source_url", "link", "id"))
        match = DOI_RE.search(blob)
        doi = match.group(0) if match else ""
    if doi:
        return "doi:" + doi.lower().rstrip(".")

    arxiv = _first_str(row, ("arxiv_id", "arxiv", "paper_id"))
    if not arxiv:
        blob = " ".join(str(row.get(k) or "") for k in ("url", "source_url", "link", "id"))
        match = ARXIV_RE.search(blob)
        arxiv = match.group(1) if match else ""
    match = ARXIV_RE.search(arxiv) if arxiv else None
    if match:
        return "arxiv:" + match.group(1).lower()

    url = _clean_url(_first_str(row, ("url", "source_url", "link", "canonical_url")))
    if url:
        match = OPENREVIEW_RE.search(url)
        if match:
            return "openreview:" + match.group(1)
        return "url:" + url

    title_key = _title_fingerprint(_first_str(row, ("title", "name")))
    if title_key:
        return "title:" + title_key
    row_blob = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return "row:" + hashlib.sha256(row_blob.encode("utf-8")).hexdigest()[:16]


def build_editor_report(paths: list[Path]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    input_counts: dict[str, int] = {}
    for path in paths:
        rows = _read_identity_rows(path)
        input_counts[str(path)] = len(rows)
        for index, row in enumerate(rows):
            grouped[_canonical_key(row)].append(
                {
                    "input_path": str(path),
                    "row_index": index,
                    "title": _first_str(row, ("title", "name", "article_title"))[:180],
                    "url": _clean_url(_first_str(row, ("url", "source_url", "link", "canonical_url"))),
                }
            )
    duplicate_groups = [
        {"canonical_key": key, "count": len(rows), "items": rows}
        for key, rows in sorted(grouped.items())
        if len(rows) > 1
    ]
    return {
        "agent_id": "jiphyeonjeon-editor",
        "agent_name": "집현전-편집자",
        "generated_at": _generated_at(),
        "input_counts": input_counts,
        "item_count": sum(input_counts.values()),
        "canonical_count": len(grouped),
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_groups": duplicate_groups,
        "no_mutation": True,
        "advisory_only": True,
        "requires_human_promotion_review": True,
    }


def _load_advisor_artifact(path: Path) -> tuple[str, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".json", ".jsonl"}:
        return text, _read_json_rows(path)
    return text, []


def _urls_from_row(row: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("url", "source_url", "link", "canonical_url", "doi_url", "pdf_url"):
        value = row.get(key)
        if value:
            urls.extend(URL_RE.findall(str(value)))
    for value in row.values():
        if isinstance(value, str):
            urls.extend(URL_RE.findall(value))
    return sorted(set(url.rstrip(".,") for url in urls))


def _domain(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _is_evidence_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    domain = parts.netloc.lower()
    if domain in NON_EVIDENCE_DOMAINS:
        return False
    if parts.path.lower().endswith(NON_EVIDENCE_EXTENSIONS):
        return False
    return parts.scheme in {"http", "https"} and bool(domain)


def build_advisor_report(path: Path, *, min_evidence: int, min_domains: int) -> dict[str, Any]:
    text, rows = _load_advisor_artifact(path)
    text_urls = [url for url in sorted(set(url.rstrip(".,") for url in URL_RE.findall(text))) if _is_evidence_url(url)]
    row_urls = [[url for url in _urls_from_row(row) if _is_evidence_url(url)] for row in rows]
    row_count = len(rows)
    evidenced_rows = sum(1 for urls in row_urls if urls)
    evidence_coverage_pct = (
        100
        if row_count == 0 and text_urls
        else (round((evidenced_rows / row_count) * 100) if row_count else 0)
    )
    domains = sorted({domain for url in text_urls for domain in [_domain(url)] if domain})
    issues: list[str] = []
    if len(text_urls) < min_evidence:
        issues.append(f"evidence_url_count_below_{min_evidence}")
    if len(domains) < min_domains:
        issues.append(f"source_diversity_below_{min_domains}")
    if row_count and evidence_coverage_pct < 80:
        issues.append("row_evidence_coverage_below_80_pct")
    overclaims = sorted(set(match.group(0).lower() for match in OVERCLAIM_RE.finditer(text)))
    if overclaims:
        issues.append("possible_overclaim_language")
    if not issues:
        status = "pass"
    elif len(text_urls) == 0 or evidence_coverage_pct < 50:
        status = "fail"
    else:
        status = "needs_review"
    return {
        "agent_id": "jiphyeonjeon-advisor",
        "agent_name": "집현전-지도교수",
        "generated_at": _generated_at(),
        "artifact_path": str(path),
        "quality_status": status,
        "quality_gate_passed": status == "pass",
        "publication_blocked": status != "pass",
        "advisory_only": True,
        "requires_human_promotion_review": True,
        "evidence_url_count": len(text_urls),
        "source_diversity": len(domains),
        "source_domains": domains,
        "row_count": row_count,
        "evidenced_row_count": evidenced_rows,
        "evidence_coverage_pct": evidence_coverage_pct,
        "possible_overclaim_terms": overclaims,
        "issues": issues,
        "no_mutation": True,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_publication_trust_gate(
    artifact: Path,
    *,
    surface: str,
    config: PublicationTrustGateConfig | None = None,
) -> dict[str, Any]:
    cfg = config or trust_gate_config_from_env()
    if not cfg.enabled:
        return {"enabled": False, "decision": "allow", "reason_codes": ["trust_gate_disabled"]}

    artifact = artifact.expanduser()
    identity_inputs = [artifact, *cfg.extra_identity_inputs]
    editor_report = build_editor_report(identity_inputs)
    advisor_report = build_advisor_report(artifact, min_evidence=cfg.min_evidence, min_domains=cfg.min_domains)
    reason_codes: list[str] = []
    if cfg.block_on_editor_duplicates and editor_report["duplicate_group_count"] > 0:
        reason_codes.append("editor_duplicate_groups")
    if cfg.block_on_advisor_non_pass and not advisor_report["quality_gate_passed"]:
        reason_codes.append(f"advisor_{advisor_report['quality_status']}")
    decision = "block" if reason_codes else "allow"
    slug = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{surface}"
    editor_path = cfg.report_dir / f"{slug}-editor.json"
    advisor_path = cfg.report_dir / f"{slug}-advisor.json"
    summary_path = cfg.report_dir / f"{slug}-summary.json"
    summary = {
        "enabled": True,
        "surface": surface,
        "artifact_path": str(artifact),
        "decision": decision,
        "reason_codes": reason_codes,
        "editor_report_path": str(editor_path),
        "advisor_report_path": str(advisor_path),
        "editor": {
            "duplicate_group_count": editor_report["duplicate_group_count"],
            "item_count": editor_report["item_count"],
        },
        "advisor": {
            "quality_status": advisor_report["quality_status"],
            "evidence_url_count": advisor_report["evidence_url_count"],
            "source_diversity": advisor_report["source_diversity"],
            "issues": advisor_report["issues"],
        },
        "no_mutation": True,
        "advisory_only": True,
    }
    _write_json(editor_path, editor_report)
    _write_json(advisor_path, advisor_report)
    _write_json(summary_path, summary)
    if decision == "block":
        raise PublicationTrustGateError(
            "Jiphyeonjeon pre-publication trust gate blocked publishing "
            f"surface={surface} reasons={','.join(reason_codes)} summary={summary_path}"
        )
    return summary
