from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from .post_newsletter import _load_dotenv
from .publication_trust_gate import PublicationTrustGateError, run_publication_trust_gate

DEFAULT_AUTHOR = "집현전 팀"
DEFAULT_BASE_URL = "https://jiphyeonjeon.kr"
DEFAULT_AUDIT_PATH = Path.home() / ".openclaw" / "state" / "discord-openclaw-bridge" / "blog-publication-audit.jsonl"
_ALLOWED_PAYLOAD_FIELDS = {"title", "slug", "excerpt", "content", "author", "tags", "thumbnail_url", "reading_time_min"}
_URL_RE = re.compile(r"https?://[^\s)\]}>'\"]+")
_UPDATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")
_DISCORD_WEBHOOK_RE = re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+")
_SECRET_KEYS_RE = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|webhook)")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?:token|secret|password|api[_-]?key)\s*[:=]\s*[^\s&]{8,}|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})"
)
_LOCAL_PATH_RE = re.compile(r"/(?:Users|private|var|tmp|home)/[^\s,}]+")
_PRIVATE_BODY_MARKERS = (
    "raw_gmail_body",
    "raw email body",
    "private newsletter body",
    "private_body",
    "raw_provider_payload",
    "raw_transcript",
    "caption_text",
    "oauth_token",
    "refresh_token",
)
_SLUG_TOKEN_RE = re.compile(r"[a-z0-9가-힣]+", re.IGNORECASE)


class BlogPublisherError(RuntimeError):
    """Raised when a blog draft cannot be safely published or previewed."""


@dataclass(frozen=True)
class BlogPublisherConfig:
    base_url: str = DEFAULT_BASE_URL
    token: str = ""
    audit_path: Path = DEFAULT_AUDIT_PATH
    timeout_sec: float = 20.0


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise BlogPublisherError(f"source file not found: {path}") from exc


def _clean_scalar(value: Any, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _slugify(value: str) -> str:
    tokens = _SLUG_TOKEN_RE.findall(value.lower())
    slug = "-".join(tokens)
    return slug[:120].strip("-") or "jiphyeonjeon-blog-post"


def compute_reading_time_min(content: str) -> int:
    # Korean technical posts on the current site are longform; use a conservative
    # mixed Korean/English heuristic and clamp to at least one minute.
    korean_chars = len(re.findall(r"[가-힣]", content))
    latin_words = len(re.findall(r"[A-Za-z0-9_]+", content))
    estimated_words = latin_words + max(1, korean_chars // 2)
    return max(1, round(estimated_words / 350))


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[text.find("\n", end + 1) + 1 :]
    metadata: dict[str, Any] = {}
    current_key = ""
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            metadata.setdefault(current_key, []).append(line[4:].strip().strip('"\''))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            metadata[key] = []
        elif value.startswith("[") and value.endswith("]"):
            metadata[key] = [part.strip().strip('"\'') for part in value[1:-1].split(",") if part.strip()]
        else:
            metadata[key] = value.strip('"\'')
    return metadata, body.lstrip()


def _first_heading(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _first_paragraph(content: str) -> str:
    paragraphs = []
    current: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("!"):
            continue
        current.append(stripped.lstrip("> ").strip())
    if current:
        paragraphs.append(" ".join(current))
    return _clean_scalar(paragraphs[0] if paragraphs else content, limit=180)


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = value.split(",")
    else:
        raw = []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _clean_scalar(item, limit=40).lstrip("#")
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags


def load_draft(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if path.suffix.lower() == ".json":
        value = json.loads(text)
        if not isinstance(value, dict):
            raise BlogPublisherError("JSON draft must be an object")
        return value

    metadata, content = _split_frontmatter(text)
    title = _clean_scalar(metadata.get("title") or _first_heading(content), limit=140)
    return {
        **metadata,
        "title": title,
        "content": content.strip(),
        "excerpt": metadata.get("excerpt") or _first_paragraph(content),
        "author": metadata.get("author") or DEFAULT_AUTHOR,
        "tags": _as_tags(metadata.get("tags")),
        "thumbnail_url": metadata.get("thumbnail_url") or metadata.get("hero_image_url") or "",
    }


def build_payload(draft: dict[str, Any]) -> dict[str, Any]:
    raw_content = draft.get("content")
    if raw_content is not None and not isinstance(raw_content, str):
        raise BlogPublisherError("draft content must be a markdown string")
    content = str(raw_content or "").strip()
    payload: dict[str, Any] = {
        "title": _clean_scalar(draft.get("title"), limit=140),
        "slug": _slugify(_clean_scalar(draft.get("slug") or draft.get("title"))),
        "excerpt": _clean_scalar(draft.get("excerpt"), limit=220),
        "content": content,
        "author": _clean_scalar(draft.get("author") or DEFAULT_AUTHOR, limit=80),
        "tags": _as_tags(draft.get("tags")),
        "reading_time_min": int(draft.get("reading_time_min") or compute_reading_time_min(content)),
    }
    thumbnail_url = _clean_scalar(draft.get("thumbnail_url"), limit=500)
    if thumbnail_url:
        payload["thumbnail_url"] = thumbnail_url
    validate_payload(payload)
    return {key: value for key, value in payload.items() if key in _ALLOWED_PAYLOAD_FIELDS}


def validate_payload(payload: dict[str, Any]) -> None:
    unknown = set(payload) - _ALLOWED_PAYLOAD_FIELDS
    if unknown:
        raise BlogPublisherError(f"payload contains unsupported fields: {sorted(unknown)}")
    required = ("title", "slug", "excerpt", "content", "author", "tags", "reading_time_min")
    missing = [key for key in required if payload.get(key) in (None, "", [])]
    if missing:
        raise BlogPublisherError(f"payload missing required fields: {', '.join(missing)}")
    if not isinstance(payload.get("tags"), list):
        raise BlogPublisherError("payload tags must be a list")
    if int(payload.get("reading_time_min", 0)) < 1:
        raise BlogPublisherError("payload reading_time_min must be positive")
    if len(_URL_RE.findall(str(payload.get("content", "")))) < 1:
        raise BlogPublisherError("payload content must include at least one public source URL")



def validate_public_payload(payload: dict[str, Any]) -> None:
    issues: list[str] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if not isinstance(value, str):
            return
        lower = value.lower()
        if _DISCORD_WEBHOOK_RE.search(value):
            issues.append(f"discord_webhook:{path}")
        if _SECRET_VALUE_RE.search(value):
            issues.append(f"secret_like_value:{path}")
        if _LOCAL_PATH_RE.search(value):
            issues.append(f"local_secret_path:{path}")
        if any(marker in lower for marker in _PRIVATE_BODY_MARKERS):
            issues.append(f"private_body_marker:{path}")

    visit(payload, "payload")
    if issues:
        raise BlogPublisherError("payload contains forbidden public content: " + ", ".join(sorted(set(issues))))


def validate_update_id(update_id: str) -> str:
    update_id = update_id.strip()
    if update_id and not _UPDATE_ID_RE.fullmatch(update_id):
        raise BlogPublisherError("--update-id must match [A-Za-z0-9_-]{1,120}")
    return update_id

def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEYS_RE.search(str(key)):
                clean[key] = "<redacted>"
            else:
                clean[key] = _sanitize(item)
        return clean
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = _LOCAL_PATH_RE.sub("<local-path>", value)
        value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", "Bearer <redacted>", value)
        value = re.sub(r"(?i)(token|secret|api[_-]?key)=([^\s&]+)", r"\1=<redacted>", value)
        return value
    return value


def _audit_record(
    *,
    decision: str,
    source: Path,
    payload: dict[str, Any],
    publish: bool,
    approval_id: str,
    trust_gate: dict[str, Any] | None,
    reason_codes: list[str] | None = None,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _sanitize(
        {
            "generated_at": _now(),
            "surface": "jiphyeonjeon-blog",
            "agent_id": "jiphyeonjeon-blog-publisher",
            "agent_name": "집현전-기자",
            "decision": decision,
            "publish_requested": publish,
            "approval_id": approval_id,
            "source_name": source.name,
            "source_sha256": _sha256_text(_read_text(source)),
            "payload": {
                "title": payload.get("title"),
                "slug": payload.get("slug"),
                "author": payload.get("author"),
                "tags": payload.get("tags"),
                "reading_time_min": payload.get("reading_time_min"),
                "has_thumbnail": bool(payload.get("thumbnail_url")),
                "content_sha256": _sha256_text(str(payload.get("content", ""))),
            },
            "trust_gate": trust_gate,
            "reason_codes": reason_codes or [],
            "response": response or {},
            "no_delete_exposed": True,
            "advisory_prerequisites_only": True,
        }
    )


def append_audit(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def config_from_env() -> BlogPublisherConfig:
    return BlogPublisherConfig(
        base_url=os.environ.get("JIPHYEONJEON_BLOG_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        token=os.environ.get("JIPHYEONJEON_BLOG_TOKEN", "").strip(),
        audit_path=Path(os.environ.get("JIPHYEONJEON_BLOG_AUDIT_PATH", str(DEFAULT_AUDIT_PATH))).expanduser(),
        timeout_sec=float(os.environ.get("JIPHYEONJEON_BLOG_TIMEOUT_SEC", "20")),
    )


def _load_approval_artifact(path: Path | None) -> str:
    if path is None:
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BlogPublisherError("approval artifact must be a JSON object")
    approved = bool(data.get("approved") or data.get("publication_approved"))
    approval_id = _clean_scalar(data.get("approval_id") or data.get("id"), limit=120)
    if not approved or not approval_id:
        raise BlogPublisherError("approval artifact must include approved=true and approval_id")
    return approval_id


def _post_or_put_payload(
    payload: dict[str, Any],
    *,
    config: BlogPublisherConfig,
    update_id: str = "",
) -> dict[str, Any]:
    if not config.token:
        raise BlogPublisherError("JIPHYEONJEON_BLOG_TOKEN is required for --publish")
    update_id = validate_update_id(update_id)
    endpoint = f"/api/blog/posts/{update_id}" if update_id else "/api/blog/posts"
    url = urljoin(config.base_url + "/", endpoint.lstrip("/"))
    headers = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json"}
    method = "PUT" if update_id else "POST"
    with httpx.Client(timeout=config.timeout_sec) as client:
        response = client.request(method, url, headers=headers, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            data = {"status_code": response.status_code, "text": response.text[:200]}
    if isinstance(data, dict):
        return data
    return {"response": data}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run or publish a Jiphyeonjeon newsroom blog draft.")
    parser.add_argument("--source", required=True, type=Path, help="Blog draft JSON or Markdown file")
    parser.add_argument("--publish", action="store_true", help="Perform POST/PUT after all approval gates pass")
    parser.add_argument("--dry-run", action="store_true", help="Explicit no-network preview; this is also the default")
    parser.add_argument("--update-id", default="", help="Existing blog post id for PUT updates; omitted means POST create")
    parser.add_argument("--approval-id", default="", help="Explicit operator approval id required for --publish")
    parser.add_argument("--approval-artifact", type=Path, help="JSON approval artifact with approved=true and approval_id")
    parser.add_argument("--audit-path", type=Path, help="Override sanitized audit JSONL path")
    parser.add_argument("--base-url", default="", help="Override Jiphyeonjeon base URL")
    parser.add_argument("--skip-dotenv", action="store_true", help="Do not load local .env")
    parser.add_argument("--print-payload", action="store_true", help="Print the Blog API payload JSON")
    return parser


def run(args: argparse.Namespace) -> int:
    if not args.skip_dotenv:
        _load_dotenv(Path(".env"))
    config = config_from_env()
    if args.base_url:
        config = BlogPublisherConfig(base_url=args.base_url.rstrip("/"), token=config.token, audit_path=config.audit_path, timeout_sec=config.timeout_sec)
    if args.audit_path:
        config = BlogPublisherConfig(base_url=config.base_url, token=config.token, audit_path=args.audit_path, timeout_sec=config.timeout_sec)

    source = args.source.expanduser()
    if args.dry_run and args.publish:
        raise BlogPublisherError("--dry-run cannot be combined with --publish")
    validate_update_id(args.update_id)
    payload = build_payload(load_draft(source))
    validate_public_payload(payload)
    approval_id = args.approval_id or _load_approval_artifact(args.approval_artifact)

    trust_summary: dict[str, Any] | None = None
    try:
        trust_summary = run_publication_trust_gate(source, surface="blog")
    except PublicationTrustGateError as exc:
        record = _audit_record(
            decision="blocked",
            source=source,
            payload=payload,
            publish=args.publish,
            approval_id=approval_id,
            trust_gate=None,
            reason_codes=[str(exc)],
        )
        append_audit(config.audit_path, record)
        print(f"blocked blog publish trust_gate {exc}", file=sys.stderr)
        return 2

    if args.print_payload or not args.publish:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))

    if not args.publish:
        append_audit(
            config.audit_path,
            _audit_record(
                decision="dry_run",
                source=source,
                payload=payload,
                publish=False,
                approval_id=approval_id,
                trust_gate=trust_summary,
                reason_codes=["dry_run_default_no_network_write"],
            ),
        )
        return 0

    if not approval_id:
        append_audit(
            config.audit_path,
            _audit_record(
                decision="blocked",
                source=source,
                payload=payload,
                publish=True,
                approval_id="",
                trust_gate=trust_summary,
                reason_codes=["missing_operator_approval_id"],
            ),
        )
        raise BlogPublisherError("--publish requires --approval-id or approved --approval-artifact")
    if not config.token:
        append_audit(
            config.audit_path,
            _audit_record(
                decision="blocked",
                source=source,
                payload=payload,
                publish=True,
                approval_id=approval_id,
                trust_gate=trust_summary,
                reason_codes=["missing_jiphyeonjeon_blog_token"],
            ),
        )
        raise BlogPublisherError("JIPHYEONJEON_BLOG_TOKEN is required for --publish")

    response = _post_or_put_payload(payload, config=config, update_id=args.update_id)
    append_audit(
        config.audit_path,
        _audit_record(
            decision="published",
            source=source,
            payload=payload,
            publish=True,
            approval_id=approval_id,
            trust_gate=trust_summary,
            response={"id": response.get("id"), "slug": response.get("slug"), "status": "ok"},
        ),
    )
    print(json.dumps(_sanitize({"status": "published", "id": response.get("id"), "slug": response.get("slug")}), ensure_ascii=False))
    return 0


def main() -> None:
    try:
        raise SystemExit(run(build_arg_parser().parse_args()))
    except BlogPublisherError as exc:
        print(f"blog publisher error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
