from __future__ import annotations

import json

from discord_openclaw_bridge.youtube_video import (
    YouTubeVideoReport,
    build_official_caption_ephemeral_analysis,
    build_official_caption_gate_analysis,
    build_unavailable_report,
    fetch_youtube_channel_video_urls,
    parse_youtube_channel_url,
    parse_youtube_url,
    sanitize_content_analysis,
    sanitize_media,
)


def test_parse_supported_youtube_forms_share_same_identity() -> None:
    urls = [
        "https://www.youtube.com/watch?v=abc123XYZ09",
        "https://youtu.be/abc123XYZ09",
        "https://www.youtube.com/shorts/abc123XYZ09",
        "https://www.youtube.com/embed/abc123XYZ09",
    ]
    identities = [parse_youtube_url(url) for url in urls]

    assert {identity.video_id for identity in identities if identity} == {"abc123XYZ09"}
    assert {identity.canonical_url for identity in identities if identity} == {
        "https://www.youtube.com/watch?v=abc123XYZ09"
    }


def test_parse_preserves_start_seconds_and_playlist_outside_canonical() -> None:
    identity = parse_youtube_url("https://youtu.be/abc123XYZ09?t=1m30s&list=PLabc")

    assert identity is not None
    assert identity.video_id == "abc123XYZ09"
    assert identity.start_seconds == 90
    assert identity.playlist_id == "PLabc"
    assert identity.canonical_url == "https://www.youtube.com/watch?v=abc123XYZ09"


def test_channel_url_is_not_a_video_identity() -> None:
    assert parse_youtube_url("https://www.youtube.com/@dsba2979") is None


def test_sanitize_media_omits_raw_provider_payload() -> None:
    media = sanitize_media(
        {
            "type": "video",
            "platform": "youtube",
            "video_id": "abc123XYZ09",
            "raw_provider_payload": {"secret": "x"},
            "transcript": "private transcript",
            "analysis_provenance": "metadata_only",
        }
    )

    dumped = str(media)
    assert media["video_id"] == "abc123XYZ09"
    assert "raw_provider_payload" not in dumped
    assert "private transcript" not in dumped


def test_unavailable_report_labels_no_provider() -> None:
    report = build_unavailable_report("https://www.youtube.com/watch?v=abc123XYZ09")

    assert isinstance(report, YouTubeVideoReport)
    assert report.analysis_status == "metadata_unavailable"
    assert report.media()["analysis_status"] == "metadata_unavailable"


def test_parse_sanitizes_original_url_sensitive_query() -> None:
    identity = parse_youtube_url("https://www.youtube.com/watch?v=abc123XYZ09&t=90&token=SECRET&utm_source=x&list=PLabc")

    assert identity is not None
    assert identity.original_url == "https://www.youtube.com/watch?v=abc123XYZ09&list=PLabc&t=90"
    assert "SECRET" not in identity.original_url
    assert "utm_source" not in identity.original_url


def test_private_status_report_degrades_metadata_ready(monkeypatch) -> None:
    from io import BytesIO
    from discord_openclaw_bridge import youtube_video

    class FakeResponse:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, _limit):
            return json.dumps(
                {
                    "items": [
                        {
                            "etag": "etag-private",
                            "snippet": {"title": "Private LLM benchmark", "description": "agent research", "channelTitle": "DSBA", "publishedAt": "2026-05-19T00:00:00Z"},
                            "contentDetails": {"duration": "PT1M"},
                            "status": {"privacyStatus": "private", "embeddable": False},
                        }
                    ]
                }
            ).encode()

    monkeypatch.setattr(youtube_video, "urlopen", lambda *args, **kwargs: FakeResponse())
    report = youtube_video.fetch_youtube_metadata_report("https://www.youtube.com/watch?v=abc123XYZ09", api_key="key")

    assert report is not None
    assert report.analysis_status == "metadata_unavailable"
    assert report.title == ""


def test_sanitize_content_analysis_keeps_canonical_fields_only() -> None:
    sanitized = sanitize_content_analysis(
        {
            "version": "ignored",
            "status": "ready",
            "analysis_status": "ready",
            "evidence_tier": "metadata_only",
            "analysis_provenance": "metadata_only",
            "provider": "youtube_data_api",
            "summary_lines": ["공개 메타데이터 기준", "https://example.com/?token=SECRET"],
            "claims": [{"text": "공개 제목: LLM benchmark", "basis": "metadata.title", "confidence": 0.4}],
            "limitations": ["자막/transcript 근거 아님"],
            "quota_units": 1,
            "confidence": 0.5,
            "operator_note_used": False,
            "source_separation": "metadata",
            "raw_provider_payload": {"secret": "x"},
            "extra_provider_blob": "drop me",
        }
    )

    assert sanitized["version"] == "youtube_content_analysis_v1"
    assert sanitized["analysis_status"] == "ready"
    assert "status" not in sanitized
    assert "extra_provider_blob" not in sanitized
    assert "token=SECRET" not in json.dumps(sanitized)
    assert sanitized["claims"] == [{"text": "공개 제목: LLM benchmark", "basis": "metadata.title", "confidence": 0.4}]


def test_sanitize_content_analysis_removes_forbidden_nested_keys_and_sensitive_values() -> None:
    sanitized = sanitize_content_analysis(
        {
            "analysis_status": "ready",
            "evidence_tier": "metadata_only",
            "summary_lines": ["ok", "bad credential=SECRET"],
            "claims": [
                {"text": "safe claim", "basis": "metadata.description", "raw_transcript": "secret words"},
                {"text": "bad access_token=SECRET", "basis": "metadata.description"},
            ],
            "limitations": ["raw transcript/audio/video는 저장하지 않음"],
            "policy_flags": ["no_raw_transcript_persisted"],
            "private_body": "secret",
        }
    )

    dumped = json.dumps(sanitized, ensure_ascii=False)
    assert "private_body" not in dumped
    assert "secret words" not in dumped
    assert "credential=SECRET" not in dumped
    assert "access_token=SECRET" not in dumped
    assert sanitized["claims"] == [{"text": "safe claim", "basis": "metadata.description"}]


def test_sanitize_content_analysis_normalizes_legacy_model_tier() -> None:
    sanitized = sanitize_content_analysis(
        {
            "analysis_status": "shadow",
            "evidence_tier": "gemini_youtube_uri_no_transcript",
            "analysis_provenance": "gemini_youtube_uri_no_transcript",
            "provider": "gemini",
            "summary_lines": ["provider-derived audiovisual inference", "자막/transcript 근거 아님"],
        }
    )

    assert sanitized["evidence_tier"] == "model_public_youtube_av_no_raw"
    assert sanitized["analysis_provenance"] == "gemini_public_youtube_av_no_raw"
    assert sanitized["source_separation"] == "provider_model"


def test_metadata_only_content_analysis_has_honesty_label_and_no_direct_speech() -> None:
    report = build_unavailable_report("https://www.youtube.com/watch?v=abc123XYZ09")

    analysis = report.content_analysis()

    assert analysis["analysis_status"] == "unavailable"
    assert analysis["evidence_tier"] == "metadata_only"
    dumped = json.dumps(analysis, ensure_ascii=False)
    assert "자막/transcript 근거 아님" in dumped
    assert "영상에서 말했다" not in dumped
    assert analysis["operator_note_used"] is False


def test_sanitize_content_analysis_drops_forbidden_marker_strings_but_keeps_honesty_labels() -> None:
    sanitized = sanitize_content_analysis(
        {
            "analysis_status": "ready",
            "evidence_tier": "metadata_only",
            "summary_lines": [
                "safe metadata summary",
                "raw_provider_payload: should not persist",
                "raw_transcript: private words",
            ],
            "claims": [
                {"text": "caption_text: private caption", "basis": "metadata.description"},
                {"text": "safe claim", "basis": "raw_caption: hidden"},
            ],
            "limitations": ["자막/transcript 근거 아님", "audio_bytes: hidden"],
            "fallback_reason": "private_body: leaked",
        }
    )

    dumped = json.dumps(sanitized, ensure_ascii=False)
    assert "raw_provider_payload:" not in dumped
    assert "raw_transcript:" not in dumped
    assert "caption_text:" not in dumped
    assert "raw_caption:" not in dumped
    assert "audio_bytes:" not in dumped
    assert "private_body:" not in dumped
    assert "자막/transcript 근거 아님" in dumped
    assert sanitized["claims"] == [{"text": "safe claim"}]


def test_sanitize_media_rebuilds_canonical_url_with_sensitive_query() -> None:
    media = sanitize_media(
        {
            "type": "video",
            "platform": "youtube",
            "video_id": "abc123XYZ09",
            "canonical_url": "https://www.youtube.com/watch?v=abc123XYZ09&key=SECRET&auth=x",
        }
    )

    assert media["canonical_url"] == "https://www.youtube.com/watch?v=abc123XYZ09"
    assert "SECRET" not in json.dumps(media)


def test_official_caption_gate_blocks_without_scope_or_quota_without_raw_caption() -> None:
    no_scope = build_official_caption_gate_analysis(
        oauth_scopes=(),
        has_edit_permission=True,
        quota_budget_units=250,
        caption_track_available=True,
    )
    low_quota = build_official_caption_gate_analysis(
        oauth_scopes=("https://www.googleapis.com/auth/youtube.force-ssl",),
        has_edit_permission=True,
        quota_budget_units=249,
        caption_track_available=True,
    )

    assert no_scope["evidence_tier"] == "official_caption_unavailable"
    assert no_scope["fallback_reason"] == "missing_oauth_scope"
    assert low_quota["fallback_reason"] == "insufficient_caption_quota_budget"
    dumped = json.dumps({"no_scope": no_scope, "low_quota": low_quota}, ensure_ascii=False)
    assert "caption_text" not in dumped
    assert "raw_caption" not in dumped


def test_official_caption_ephemeral_analysis_records_250_quota_and_no_raw_caption() -> None:
    analysis = build_official_caption_ephemeral_analysis(
        summary_lines=["공식 caption 기반 요약"],
        claims=[{"text": "caption-derived concise claim", "basis": "official_caption", "confidence": 0.7}],
        caption_track_id_hash="track_hash_123",
        caption_language="ko",
    )

    assert analysis["evidence_tier"] == "official_caption_ephemeral"
    assert analysis["quota_units"] == 250
    assert any("ephemeral_deleted=true" == flag for flag in analysis["policy_flags"])
    assert any(str(flag).startswith("caption_track_id_hash:") for flag in analysis["policy_flags"])
    dumped = json.dumps(analysis, ensure_ascii=False)
    assert "raw_caption" not in dumped
    assert "caption_text" not in dumped


def test_official_caption_gate_records_provider_error_classes() -> None:
    for error_class in ("401", "403", "404", "quota_exceeded", "no_caption_track"):
        analysis = build_official_caption_gate_analysis(
            oauth_scopes=("https://www.googleapis.com/auth/youtube.force-ssl",),
            has_edit_permission=True,
            quota_budget_units=250,
            caption_track_available=True,
            provider_error_class=error_class,
        )

        assert analysis["evidence_tier"] == "official_caption_unavailable"
        assert analysis["fallback_reason"] == f"caption_provider_error:{error_class}"
        assert analysis["source_separation"] == "official_caption"


def test_parse_youtube_channel_url_supported_forms() -> None:
    handle = parse_youtube_channel_url("https://www.youtube.com/@dsba2979")
    channel = parse_youtube_channel_url("https://www.youtube.com/channel/UCPq01cgCcEwhXl7BvcwIQyg")
    user = parse_youtube_channel_url("https://www.youtube.com/user/example")

    assert handle is not None and handle.handle == "@dsba2979"
    assert handle.canonical_url == "https://www.youtube.com/@dsba2979"
    assert channel is not None and channel.channel_id == "UCPq01cgCcEwhXl7BvcwIQyg"
    assert user is not None and user.username == "example"


def test_fetch_youtube_channel_video_urls_uses_official_api(monkeypatch) -> None:
    from discord_openclaw_bridge import youtube_video

    calls: list[str] = []

    class FakeResponse:
        def __init__(self, payload: dict):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, _limit):
            return json.dumps(self.payload).encode()

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        calls.append(url)
        if "/channels?" in url:
            assert "forHandle=%40dsba2979" in url
            return FakeResponse({"items": [{"id": "UCPq01cgCcEwhXl7BvcwIQyg", "contentDetails": {"relatedPlaylists": {"uploads": "UUuploads"}}}]})
        if "/playlistItems?" in url:
            assert "playlistId=UUuploads" in url
            return FakeResponse({"items": [{"contentDetails": {"videoId": "XCyB6ReRoKk"}}, {"contentDetails": {"videoId": "rN5UpLmt2HM"}}]})
        raise AssertionError(url)

    monkeypatch.setattr(youtube_video, "urlopen", fake_urlopen)

    result = fetch_youtube_channel_video_urls("https://www.youtube.com/@dsba2979", api_key="key", max_results=2)

    assert result is not None
    assert result.status == "ready"
    assert result.quota_units == 2
    assert result.channel.channel_id == "UCPq01cgCcEwhXl7BvcwIQyg"
    assert result.video_urls == (
        "https://www.youtube.com/watch?v=XCyB6ReRoKk",
        "https://www.youtube.com/watch?v=rN5UpLmt2HM",
    )
    assert len(calls) == 2


def test_fetch_youtube_channel_video_urls_without_api_key_is_unavailable() -> None:
    result = fetch_youtube_channel_video_urls("https://www.youtube.com/@dsba2979", api_key="")

    assert result is not None
    assert result.status == "unavailable"
    assert result.reason == "missing_youtube_data_api_key"


def test_sanitize_content_analysis_drops_operator_context_sensitive_markers() -> None:
    sanitized = sanitize_content_analysis(
        {
            "analysis_status": "ready",
            "evidence_tier": "operator_note",
            "summary_lines": [
                "safe operator note",
                "key=SECRET",
                "api_key=SECRET",
                "auth=x",
                "password=hunter2",
                "code=abc",
                "signature=sig",
                "sig=sig",
                "relay_token=secret",
            ],
            "claims": [
                {"text": "safe claim", "basis": "operator_note"},
                {"text": "operator api_key=SECRET", "basis": "operator_note"},
            ],
        }
    )

    dumped = json.dumps(sanitized, ensure_ascii=False)
    for marker in ("key=", "api_key=", "auth=", "password=", "code=", "signature=", "sig=", "relay_token="):
        assert marker not in dumped
    assert "safe operator note" in dumped
    assert sanitized["claims"] == [{"text": "safe claim", "basis": "operator_note"}]


def test_sanitize_content_analysis_drops_colon_and_space_sensitive_markers() -> None:
    sanitized = sanitize_content_analysis(
        {
            "analysis_status": "ready",
            "evidence_tier": "operator_note",
            "summary_lines": [
                "api_key: SECRET",
                "api key: SECRET",
                "relay_token: secret",
                "relay token: secret",
                "auth: bearer",
                "password: hunter2",
                "safe note",
            ],
            "claims": [
                {"text": "signature: sig", "basis": "operator_note"},
                {"text": "safe claim", "basis": "operator_note"},
            ],
        }
    )

    dumped = json.dumps(sanitized, ensure_ascii=False)
    for marker in ("api_key:", "api key:", "relay_token:", "relay token:", "auth:", "password:", "signature:"):
        assert marker not in dumped
    assert "safe note" in dumped
    assert sanitized["claims"] == [{"text": "safe claim", "basis": "operator_note"}]
