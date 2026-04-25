from __future__ import annotations

import base64
import hashlib
import json
import re


_SAFE_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_ID_LEN = 64


def _b64url_decode(seg: str) -> bytes:
    padded = seg + "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload WITHOUT verifying the signature.

    We don't have the signing key, and the token is read from a file we
    control (not the network), so we trust the source. The decode is purely
    to read the ``sub`` claim — never used for authorization decisions.
    """
    parts = token.split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT (expected at least header.payload)")
    try:
        return json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid JWT payload: {e}") from e


def sanitize_user_id(raw: str) -> str:
    """Reduce arbitrary string to a filesystem-safe identifier.

    Unsafe characters become underscores; result is truncated to 64 chars and
    stripped of leading/trailing separators so it cannot be a dotfile or
    traverse directories.
    """
    if not isinstance(raw, str):
        raise ValueError(f"user_id must be str, got {type(raw).__name__}")
    stripped = raw.strip()
    if not stripped:
        raise ValueError("empty user_id")
    safe = _SAFE_RE.sub("_", stripped)[:_MAX_ID_LEN].strip("._-")
    if not safe:
        raise ValueError(f"could not derive safe user_id from {raw!r}")
    return safe


def user_id_from_jwt(token: str) -> str:
    """Extract a sanitized user_id from the ``sub`` claim of a JWT.

    Falls back to a short sha256 prefix of the raw token when ``sub`` is
    missing or empty, so multi-tenant file layout still keeps tokens isolated.
    """
    if not token:
        raise ValueError("empty token")
    try:
        payload = decode_jwt_payload(token)
        sub = payload.get("sub")
        if isinstance(sub, str) and sub.strip():
            return sanitize_user_id(sub)
    except ValueError:
        pass

    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return f"anon_{digest}"
