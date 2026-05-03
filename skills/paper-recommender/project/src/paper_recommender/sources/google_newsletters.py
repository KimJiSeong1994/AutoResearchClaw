"""Local Google newsletter source adapter.

This adapter intentionally avoids live Gmail access. The safe supported input is
an operator-provided local mbox export, such as Google Takeout's Mail export,
configured by path. It reads only configured files, never stores credentials,
and emits one candidate per allowlisted newsletter issue for daily research.

Privacy boundary: email bodies are scanned only in memory for topic and URL
matching. Raw body text is not emitted into CandidateItem fields because those
fields flow into artifacts, wiki pages, and LLM prompts.
"""

from __future__ import annotations

import logging
import mailbox
import re
from dataclasses import dataclass, field
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

from paper_recommender.sources import CandidateItem, SourceLimits
from paper_recommender.sources._util import normalize_title_for_dedup

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\]\[\"']+", re.IGNORECASE)
_RESEARCH_HOST_HINTS = (
    "arxiv.org",
    "openreview.net",
    "doi.org",
    "aclanthology.org",
    "papers.ssrn.com",
    "semanticscholar.org",
    "github.com",
    "huggingface.co",
)
_TRACKING_HOST_HINTS = (
    "mailchi.mp",
    "list-manage.com",
    "sendgrid.net",
    "mandrillapp.com",
    "mailgun.org",
    "click.convertkit-mail.com",
    "substack.com/redirect",
)
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "source",
}


@dataclass(frozen=True)
class GoogleNewsletterSettings:
    """Config for local exported Google-account newsletter ingestion.

    ``mbox_paths`` should point at local mbox files (for example Google Takeout
    exports). At least one sender or subject allowlist entry is required by the
    adapter before it scans any mbox, so private mail is not swept into the
    research pipeline accidentally.
    """

    mbox_paths: list[str] = field(default_factory=list)
    sender_allowlist: list[str] = field(default_factory=list)
    subject_allowlist: list[str] = field(default_factory=list)
    max_messages: int = 200
    max_mbox_bytes: int = 50 * 1024 * 1024


class GoogleNewsletterMboxAdapter:
    name = "google_newsletters"

    def __init__(self, settings: GoogleNewsletterSettings) -> None:
        self._settings = settings
        self._sender_allowlist = [
            s.lower().strip() for s in settings.sender_allowlist if s.strip()
        ]
        self._subject_allowlist = [
            s.lower().strip() for s in settings.subject_allowlist if s.strip()
        ]

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        if not self._sender_allowlist and not self._subject_allowlist:
            log.warning(
                "google_newsletters disabled: sender_allowlist or subject_allowlist is required"
            )
            return []

        topics = [t.lower().strip() for t in seed_topics if t.strip()]
        items: list[CandidateItem] = []
        seen: set[str] = set()
        scanned = 0

        for raw_path in self._settings.mbox_paths:
            if (
                len(items) >= limits.max_per_source
                or scanned >= self._settings.max_messages
            ):
                break
            path = Path(raw_path).expanduser()
            safe_path = _validated_mbox_path(path, self._settings.max_mbox_bytes)
            if safe_path is None:
                continue
            try:
                box = mailbox.mbox(safe_path, create=False)
            except (OSError, mailbox.Error) as e:
                log.warning(
                    "google_newsletters cannot open configured mbox %s: %s",
                    _redacted_path(path),
                    e,
                )
                continue

            try:
                for msg in box:
                    if (
                        len(items) >= limits.max_per_source
                        or scanned >= self._settings.max_messages
                    ):
                        break
                    scanned += 1
                    item = self._message_to_item(msg, topics, limits)
                    if item is None:
                        continue
                    key = item.url or normalize_title_for_dedup(item.title)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append(item)
            finally:
                box.close()
        return items

    def _message_to_item(
        self,
        msg: Message,
        topics: list[str],
        limits: SourceLimits,
    ) -> CandidateItem | None:
        subject = _decode_header_text(msg.get("Subject", "")).strip()
        if not subject:
            return None
        senders = [
            addr.lower()
            for _name, addr in getaddresses(msg.get_all("From", []))
            if addr
        ]
        if self._sender_allowlist and not _matches_any(senders, self._sender_allowlist):
            return None
        if self._subject_allowlist and not any(
            k in subject.lower() for k in self._subject_allowlist
        ):
            return None

        dt = None
        try:
            dt = parsedate_to_datetime(msg.get("Date", ""))
        except (TypeError, ValueError, IndexError):
            dt = None
        year = dt.year if dt else None
        if limits.year_from and year and year < limits.year_from:
            return None

        body = _plain_text_body(msg)
        haystack = f"{subject}\n{body[:3000]}".lower()
        if topics and not any(t in haystack for t in topics):
            return None

        url = _best_research_url(body)
        if url is None:
            return None

        sender_label = senders[0] if senders else "Google newsletter export"
        abstract = _metadata_summary(sender_label=sender_label, year=year, url=url)
        return CandidateItem(
            source=self.name,
            title=_clean_subject(subject),
            url=url,
            abstract=abstract,
            authors=(sender_label,),
            year=year,
            venue="Google newsletter export",
            tags=("newsletter", "google-takeout"),
            score=1.0,
            **({"fetched_at": dt} if dt is not None else {}),
        )


def _validated_mbox_path(path: Path, max_bytes: int) -> Path | None:
    display = _redacted_path(path)
    if path.name.endswith(".icloud"):
        log.warning(
            "google_newsletters mbox skipped because it is an iCloud placeholder: %s",
            display,
        )
        return None
    if path.is_symlink():
        log.warning("google_newsletters mbox symlink rejected: %s", display)
        return None
    if not path.is_file():
        log.warning("google_newsletters mbox path missing or not a file: %s", display)
        return None
    try:
        real = path.resolve(strict=True)
        st = real.stat()
    except OSError as e:
        log.warning("google_newsletters cannot stat configured mbox %s: %s", display, e)
        return None
    if st.st_size > max_bytes:
        log.warning(
            "google_newsletters mbox skipped because it exceeds max_mbox_bytes: %s",
            display,
        )
        return None
    return real


def _redacted_path(path: Path) -> str:
    return f".../{path.name}" if path.name else "...(unnamed)"


def _matches_any(values: list[str], allowlist: list[str]) -> bool:
    for val in values:
        for allowed in allowlist:
            if val == allowed or val.endswith("@" + allowed) or allowed in val:
                return True
    return False


def _decode_header_text(value: str) -> str:
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _plain_text_body(msg: Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype != "text/plain" or "attachment" in disp:
                continue
            parts.append(_payload_to_text(part))
    elif msg.get_content_type() == "text/plain":
        parts.append(_payload_to_text(msg))
    return "\n".join(p for p in parts if p)


def _payload_to_text(msg: Message) -> str:
    payload = msg.get_payload(decode=True)
    if payload is None:
        raw = msg.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _best_research_url(text: str) -> str | None:
    for raw_url in _URL_RE.findall(text or ""):
        cleaned = _sanitize_research_url(raw_url.rstrip(".,;:)"))
        if cleaned:
            return cleaned
    return None


def _sanitize_research_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    host_and_path = f"{host}{parsed.path}".lower()
    if any(h in host_and_path for h in _TRACKING_HOST_HINTS):
        redirected = _extract_redirect_target(parsed)
        if redirected:
            return _sanitize_research_url(redirected)
        return None
    if not any(host == h or host.endswith("." + h) for h in _RESEARCH_HOST_HINTS):
        return None

    kept: list[tuple[str, str]] = []
    for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
        key_lc = key.lower()
        if key_lc in _TRACKING_QUERY_KEYS or any(
            key_lc.startswith(p) for p in _TRACKING_QUERY_PREFIXES
        ):
            continue
        kept.extend((key, v) for v in values)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", urlencode(kept), "")
    )


def _extract_redirect_target(parsed) -> str | None:
    query = parse_qs(parsed.query)
    for key in ("url", "u", "target", "redirect"):
        vals = query.get(key)
        if vals:
            return unquote(vals[0])
    return None


def _clean_subject(subject: str) -> str:
    return re.sub(r"^\s*(re|fwd?):\s*", "", subject, flags=re.IGNORECASE).strip()


def _metadata_summary(*, sender_label: str, year: int | None, url: str) -> str:
    host = urlparse(url).netloc.lower() or "research link"
    bits = [
        f"Allowlisted Google newsletter issue from {sender_label}.",
        f"Research link host: {host}.",
    ]
    if year:
        bits.append(f"Message year: {year}.")
    bits.append("Email body intentionally omitted from artifacts and prompts.")
    return " ".join(bits)


__all__ = ["GoogleNewsletterMboxAdapter", "GoogleNewsletterSettings"]
