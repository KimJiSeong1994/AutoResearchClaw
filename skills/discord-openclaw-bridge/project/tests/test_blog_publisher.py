from __future__ import annotations

import json
from pathlib import Path

import pytest

from discord_openclaw_bridge import blog_publisher
from discord_openclaw_bridge.blog_publisher import BlogPublisherConfig, BlogPublisherError


def _draft(tmp_path: Path) -> Path:
    path = tmp_path / "draft.md"
    path.write_text(
        """---
title: 집현전 기자 테스트 포스트
excerpt: 근거 기반 블로그 게시 테스트입니다.
author: 집현전 팀
tags: [테스트, 블로그]
---
# 집현전 기자 테스트 포스트

> 3줄 요약
> 1. 무엇이 바뀌었는지 설명합니다.
> 2. 공개 근거를 확인합니다.
> 3. 게시 전 안전 장치를 둡니다.

## 왜 지금인가
공개 근거는 https://example.com/research 와 https://research.example.org/report 에서 확인합니다.

## 출처
- https://example.com/research
- https://research.example.org/report
""",
        encoding="utf-8",
    )
    return path


def test_build_payload_from_markdown_frontmatter(tmp_path: Path) -> None:
    payload = blog_publisher.build_payload(blog_publisher.load_draft(_draft(tmp_path)))

    assert payload["title"] == "집현전 기자 테스트 포스트"
    assert payload["author"] == "집현전 팀"
    assert payload["tags"] == ["테스트", "블로그"]
    assert payload["reading_time_min"] >= 1
    assert "# 집현전 기자 테스트 포스트\n\n> 3줄 요약" in payload["content"]
    assert "- https://example.com/research" in payload["content"]
    assert set(payload) <= {"title", "slug", "excerpt", "content", "author", "tags", "thumbnail_url", "reading_time_min"}


def test_dry_run_writes_sanitized_audit_without_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _draft(tmp_path)
    audit = tmp_path / "audit.jsonl"

    def fail_http(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("dry-run attempted HTTP write")

    monkeypatch.setattr(blog_publisher, "_post_or_put_payload", fail_http)
    monkeypatch.setenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(tmp_path / "reports"))

    rc = blog_publisher.run(
        blog_publisher.build_arg_parser().parse_args(
            ["--source", str(source), "--audit-path", str(audit), "--skip-dotenv", "--dry-run", "--print-payload"]
        )
    )

    assert rc == 0
    assert "집현전 기자 테스트 포스트" in capsys.readouterr().out
    record = json.loads(audit.read_text(encoding="utf-8").strip())
    assert record["decision"] == "dry_run"
    assert record["agent_name"] == "집현전-기자"
    assert "/Users/" not in json.dumps(record, ensure_ascii=False)
    assert "token" not in json.dumps(record.get("payload", {}), ensure_ascii=False).lower()


def test_publish_requires_operator_approval_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = _draft(tmp_path)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("JIPHYEONJEON_BLOG_TOKEN", "test-token")
    monkeypatch.setenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(tmp_path / "reports"))

    with pytest.raises(BlogPublisherError, match="approval"):
        blog_publisher.run(
            blog_publisher.build_arg_parser().parse_args(
                ["--source", str(source), "--audit-path", str(audit), "--skip-dotenv", "--publish"]
            )
        )

    record = json.loads(audit.read_text(encoding="utf-8").strip())
    assert record["decision"] == "blocked"
    assert "missing_operator_approval_id" in record["reason_codes"]


def test_publish_calls_post_only_after_guards(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = _draft(tmp_path)
    audit = tmp_path / "audit.jsonl"
    calls = []

    def fake_post(payload, *, config: BlogPublisherConfig, update_id: str = ""):
        calls.append({"payload": payload, "token": config.token, "update_id": update_id})
        return {"id": "post123", "slug": payload["slug"]}

    monkeypatch.setenv("JIPHYEONJEON_BLOG_TOKEN", "test-token")
    monkeypatch.setenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(blog_publisher, "_post_or_put_payload", fake_post)

    rc = blog_publisher.run(
        blog_publisher.build_arg_parser().parse_args(
            [
                "--source",
                str(source),
                "--audit-path",
                str(audit),
                "--skip-dotenv",
                "--publish",
                "--approval-id",
                "approval-123",
            ]
        )
    )

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["token"] == "test-token"
    record = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert record["decision"] == "published"
    assert record["approval_id"] == "approval-123"


def test_trust_gate_blocks_before_publish(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "thin.md"
    source.write_text("---\ntitle: 얇은 글\nexcerpt: 근거 없는 주장입니다.\ntags: [테스트]\n---\n# 얇은 글\n\n근거 없는 revolutionary 주장입니다. 참고 링크는 https://example.com/only-one 입니다.", encoding="utf-8")
    audit = tmp_path / "audit.jsonl"

    def fail_http(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("blocked publish attempted HTTP write")

    monkeypatch.setenv("JIPHYEONJEON_BLOG_TOKEN", "test-token")
    monkeypatch.setenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(blog_publisher, "_post_or_put_payload", fail_http)

    rc = blog_publisher.run(
        blog_publisher.build_arg_parser().parse_args(
            [
                "--source",
                str(source),
                "--audit-path",
                str(audit),
                "--skip-dotenv",
                "--publish",
                "--approval-id",
                "approval-123",
            ]
        )
    )

    assert rc == 2
    assert json.loads(audit.read_text(encoding="utf-8"))["decision"] == "blocked"


def test_no_delete_surface_exposed() -> None:
    parser_help = blog_publisher.build_arg_parser().format_help().lower()
    source = Path(blog_publisher.__file__).read_text(encoding="utf-8").lower()
    assert "delete" not in parser_help
    assert ".delete(" not in source
    assert "method = \"delete\"" not in source


def test_dry_run_and_publish_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(BlogPublisherError, match="dry-run"):
        blog_publisher.run(
            blog_publisher.build_arg_parser().parse_args(
                ["--source", str(_draft(tmp_path)), "--skip-dotenv", "--dry-run", "--publish", "--approval-id", "approval-123"]
            )
        )


def test_invalid_update_id_is_rejected_before_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = _draft(tmp_path)

    def fail_http(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("invalid update id attempted HTTP write")

    monkeypatch.setenv("JIPHYEONJEON_BLOG_TOKEN", "test-token")
    monkeypatch.setattr(blog_publisher, "_post_or_put_payload", fail_http)

    with pytest.raises(BlogPublisherError, match="update-id"):
        blog_publisher.run(
            blog_publisher.build_arg_parser().parse_args(
                [
                    "--source",
                    str(source),
                    "--skip-dotenv",
                    "--publish",
                    "--approval-id",
                    "approval-123",
                    "--update-id",
                    "../admin/users",
                ]
            )
        )


def test_forbidden_public_payload_is_blocked_before_print_or_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "secret.md"
    source.write_text(
        "---\ntitle: 보안 사고 테스트\nexcerpt: 차단되어야 합니다.\ntags: [보안]\n---\n# 보안 사고 테스트\n\n토큰 token=abcdefghi123456789 와 https://example.com/source 를 포함합니다.",
        encoding="utf-8",
    )

    def fail_http(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("forbidden payload attempted HTTP write")

    monkeypatch.setattr(blog_publisher, "_post_or_put_payload", fail_http)

    with pytest.raises(BlogPublisherError, match="forbidden public content"):
        blog_publisher.run(
            blog_publisher.build_arg_parser().parse_args(
                ["--source", str(source), "--skip-dotenv", "--dry-run", "--print-payload"]
            )
        )
    assert "abcdefghi" not in capsys.readouterr().out


def test_private_body_markers_are_blocked_before_print(tmp_path: Path) -> None:
    for marker in ("private_body", "raw_provider_payload", "raw_transcript", "caption_text"):
        source = tmp_path / f"{marker}.md"
        source.write_text(
            f"---\ntitle: private marker\nexcerpt: blocked\ntags: [보안]\n---\n# private marker\n\n{marker}: secret text with https://example.com/source",
            encoding="utf-8",
        )
        with pytest.raises(BlogPublisherError, match="forbidden public content"):
            blog_publisher.run(
                blog_publisher.build_arg_parser().parse_args(
                    ["--source", str(source), "--skip-dotenv", "--dry-run", "--print-payload"]
                )
            )


def test_json_draft_content_must_be_markdown_string(tmp_path: Path) -> None:
    source = tmp_path / "nested.json"
    source.write_text(
        json.dumps(
            {
                "title": "Nested",
                "excerpt": "Nested",
                "content": {"private_body": "secret", "url": "https://example.com/source"},
                "author": "집현전 팀",
                "tags": ["보안"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BlogPublisherError, match="markdown string"):
        blog_publisher.build_payload(blog_publisher.load_draft(source))


def test_missing_publish_token_is_audited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = _draft(tmp_path)
    audit = tmp_path / "audit.jsonl"
    monkeypatch.delenv("JIPHYEONJEON_BLOG_TOKEN", raising=False)
    monkeypatch.setenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(tmp_path / "reports"))

    with pytest.raises(BlogPublisherError, match="TOKEN"):
        blog_publisher.run(
            blog_publisher.build_arg_parser().parse_args(
                [
                    "--source",
                    str(source),
                    "--audit-path",
                    str(audit),
                    "--skip-dotenv",
                    "--publish",
                    "--approval-id",
                    "approval-123",
                ]
            )
        )
    record = json.loads(audit.read_text(encoding="utf-8").strip())
    assert record["decision"] == "blocked"
    assert "missing_jiphyeonjeon_blog_token" in record["reason_codes"]
