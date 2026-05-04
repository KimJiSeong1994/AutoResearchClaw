from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "gmail_newsletter_briefing.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("gmail_newsletter_briefing", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules["gmail_newsletter_briefing"] = mod
spec.loader.exec_module(mod)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_build_auth_url_uses_readonly_scope() -> None:
    url = mod.build_auth_url(
        mod.OAuthClient(client_id="cid", client_secret="secret"),
        redirect_uri="http://localhost:8765/oauth2callback",
    )

    assert "client_id=cid" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "gmail.readonly" in url


def test_message_to_newsletter_decodes_headers_and_text_parts() -> None:
    message = {
        "payload": {
            "headers": [
                {"name": "Subject", "value": "RAG weekly"},
                {"name": "From", "value": "Digest <digest@example.com>"},
                {"name": "Date", "value": "Mon, 04 May 2026 07:00:00 +0900"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Read https://arxiv.org/abs/2605.00001")}},
                {"mimeType": "application/pdf", "body": {"data": _b64("ignored")}},
            ],
        }
    }

    msg = mod.message_to_newsletter(message)

    assert msg.subject == "RAG weekly"
    assert msg.sender == "Digest <digest@example.com>"
    assert "https://arxiv.org/abs/2605.00001" in msg.body
    assert "ignored" not in msg.body


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self):
        self.posts = []

    def post(self, url, data):
        self.posts.append((url, data))
        return FakeResponse({"access_token": "access"})


def test_refresh_access_token_uses_refresh_token(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps({"client_id": "cid", "client_secret": "sec", "refresh_token": "refresh"}),
        encoding="utf-8",
    )
    http = FakeHttp()

    token = mod.refresh_access_token(token_path, http=http)

    assert token == "access"
    assert http.posts[0][1]["grant_type"] == "refresh_token"
