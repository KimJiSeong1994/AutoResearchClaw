from __future__ import annotations

import asyncio
import mailbox
from email.message import EmailMessage
from pathlib import Path

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.google_newsletters import (
    GoogleNewsletterMboxAdapter,
    GoogleNewsletterSettings,
)


def _write_mbox(path: Path, messages: list[EmailMessage]) -> None:
    box = mailbox.mbox(path)
    try:
        for msg in messages:
            box.add(msg)
        box.flush()
    finally:
        box.close()


def _message(
    *,
    subject: str,
    sender: str,
    body: str,
    date: str = "Sun, 03 May 2026 09:00:00 +0000",
) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "reader@example.test"
    msg["Date"] = date
    msg.set_content(body)
    return msg


def test_google_newsletter_mbox_adapter_extracts_allowed_research_issue(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="New transformer paper: https://arxiv.org/abs/2605.00001 about sparse attention.",
            ),
            _message(
                subject="Private receipt",
                sender="billing@example.test",
                body="not research",
            ),
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(mbox_path)],
            sender_allowlist=["newsletter.example"],
            subject_allowlist=["research"],
        )
    )
    out = asyncio.run(
        adapter.fetch(["transformer"], SourceLimits(max_per_source=10, year_from=2024))
    )

    assert len(out) == 1
    item = out[0]
    assert item.source == "google_newsletters"
    assert item.title == "ML Research Weekly"
    assert item.url == "https://arxiv.org/abs/2605.00001"
    assert item.authors == ("digest@newsletter.example",)
    assert item.year == 2026
    assert item.tags == ("newsletter", "google-takeout")
    assert item.abstract and "Email body intentionally omitted" in item.abstract
    assert "sparse attention" not in item.abstract


def test_google_newsletter_mbox_adapter_requires_topic_match(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="A robotics issue with https://example.test/post",
            ),
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(mbox_path)],
            sender_allowlist=["digest@newsletter.example"],
        )
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_missing_file_is_safe() -> None:
    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(mbox_paths=["/definitely/not/here.mbox"])
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_requires_allowlist(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="transformer https://arxiv.org/abs/2605.00001",
            )
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(mbox_paths=[str(mbox_path)])
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_rejects_symlink(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mail.mbox"
    link_path = tmp_path / "mail-link.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="transformer https://arxiv.org/abs/2605.00001",
            )
        ],
    )
    link_path.symlink_to(mbox_path)

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(link_path)],
            sender_allowlist=["newsletter.example"],
        )
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_rejects_oversized_mbox(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="transformer https://arxiv.org/abs/2605.00001",
            )
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(mbox_path)],
            sender_allowlist=["newsletter.example"],
            max_mbox_bytes=1,
        )
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_ignores_tracking_only_url(
    tmp_path: Path,
) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="transformer https://mailchi.mp/newsletter/click?u=personal-token",
            )
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(mbox_path)],
            sender_allowlist=["newsletter.example"],
        )
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert out == []


def test_google_newsletter_mbox_adapter_strips_tracking_query(tmp_path: Path) -> None:
    mbox_path = tmp_path / "mail.mbox"
    _write_mbox(
        mbox_path,
        [
            _message(
                subject="ML Research Weekly",
                sender="digest@newsletter.example",
                body="transformer https://arxiv.org/abs/2605.00001?utm_source=newsletter&context=keep",
            )
        ],
    )

    adapter = GoogleNewsletterMboxAdapter(
        GoogleNewsletterSettings(
            mbox_paths=[str(mbox_path)],
            sender_allowlist=["newsletter.example"],
        )
    )
    out = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=10)))

    assert len(out) == 1
    assert out[0].url == "https://arxiv.org/abs/2605.00001?context=keep"
