"""Cross-source canonicalization for deduplication.

Maps paper URLs (arxiv, nature, openreview) to deterministic string keys so
that the same paper surfaced by multiple sources is collapsed to a single item.

Priority: arxiv_id field -> doi field -> url-derived arxiv -> url-derived doi
          -> url-derived openreview -> normalized title -> ""
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from paper_recommender.sources import CandidateItem
from paper_recommender.sources._util import normalize_title_for_dedup

# ---------------------------------------------------------------------------
# Compiled regexes (module-level, single compilation)
# ---------------------------------------------------------------------------

# Matches nature.com article slugs like /articles/s41586-024-12345-6 (research)
# or /articles/d41586-024-00123-4 (news/editorial). Pattern: one lowercase
# letter + 4-5 digits + 3 groups separated by hyphens. The trailing group
# allows word chars and hyphens to accommodate occasional variant suffixes.
_NATURE_DOI_RE = re.compile(
    r"^/articles/([a-z]\d{4,5}-\d{3}-\d{4,5}-[\w-]+)$"
)
_NATURE_DOI_PREFIX = "10.1038/"

# Matches arxiv paths: abs/2401.12345, pdf/2401.12345v2, html/2401.12345
_ARXIV_RE = re.compile(
    r"^(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?)$"
)

# Matches /forum (path only; query carries ?id=XXX)
_OPENREVIEW_FORUM_RE = re.compile(r"^/forum$")


# ---------------------------------------------------------------------------
# URL -> identifier helpers
# ---------------------------------------------------------------------------


def nature_url_to_doi(url: str) -> str | None:
    """Return a DOI string for a nature.com article URL, or None.

    Handles www.nature.com and bare nature.com. Matches slugs of the form
    ``[a-z]<4-5d>-<3d>-<4-5d>-<suffix>`` (e.g. s41586-024-12345-6 for
    research articles or d41586-024-00123-4 for news/editorials).
    Collection pages and arbitrary slugs return None.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host.endswith("nature.com"):
        return None
    m = _NATURE_DOI_RE.match(parsed.path)
    return f"{_NATURE_DOI_PREFIX}{m.group(1)}" if m else None


def arxiv_url_to_id(url: str) -> str | None:
    """Return a bare arxiv ID for an arxiv.org or export.arxiv.org URL.

    Handles abs/, pdf/, html/ path prefixes. Version suffix (v1, v2 ...) is
    preserved.
    """
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host not in {"arxiv.org", "export.arxiv.org"}:
        return None
    path = parsed.path.strip("/")
    m = _ARXIV_RE.match(path)
    return m.group(1) if m else None


def openreview_url_to_forum_id(url: str) -> str | None:
    """Return the forum ID from an openreview.net /forum?id=XXX URL, or None."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "openreview.net":
        return None
    if not _OPENREVIEW_FORUM_RE.match(parsed.path):
        return None
    qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
    return qs.get("id")


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def to_canonical_key(item: CandidateItem) -> str:
    """Return a single deterministic dedup key for *item*.

    Priority (highest -> lowest):
    1. ``item.arxiv_id``           -> ``"arxiv:<id_lower>"``
    2. ``item.doi``                -> ``"doi:<doi_lower>"``
    3. URL-derived arxiv ID        -> ``"arxiv:<id_lower>"``
    4. URL-derived nature DOI      -> ``"doi:<doi_lower>"``
    5. URL-derived openreview ID   -> ``"openreview:<id_lower>"``
    6. Normalized title            -> ``"title:<normalized>"``
    7. All blank                   -> ``""``

    The frozen invariant of :class:`CandidateItem` is never violated: this
    function is a pure read -- it never mutates *item*.
    """
    if item.arxiv_id:
        return f"arxiv:{item.arxiv_id.lower()}"

    if item.doi:
        return f"doi:{item.doi.lower()}"

    if item.url:
        derived_arxiv = arxiv_url_to_id(item.url)
        if derived_arxiv:
            return f"arxiv:{derived_arxiv.lower()}"

        derived_doi = nature_url_to_doi(item.url)
        if derived_doi:
            return f"doi:{derived_doi.lower()}"

        derived_or = openreview_url_to_forum_id(item.url)
        if derived_or:
            return f"openreview:{derived_or.lower()}"

    title_key = normalize_title_for_dedup(item.title)
    return f"title:{title_key}" if title_key else ""


__all__ = [
    "arxiv_url_to_id",
    "nature_url_to_doi",
    "openreview_url_to_forum_id",
    "to_canonical_key",
]
