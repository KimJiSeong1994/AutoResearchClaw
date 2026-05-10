"""Post the latest miner-seeds run as a daily forum thread.

Reads ``~/.openclaw/workspace/state/miner-seeds-last-status.json`` produced by
``discord-openclaw-miner-seeds`` and creates one forum thread in the configured
운영리포팅 forum channel summarising the run. Designed to be invoked from the
cron runner immediately after the seed expansion CLI exits, so an abnormal
firing surfaces in Discord within seconds rather than waiting for an SSH check.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

DEFAULT_OPS_REPORT_CHANNEL_ID = "1502980129343672504"  # 운영리포팅 forum
DEFAULT_STATUS_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "miner-seeds-last-status.json"
FORUM_CHANNEL_TYPE = 15
DISCORD_MESSAGE_LIMIT = 2000
DISCORD_THREAD_TITLE_LIMIT = 90


class ReportConfigError(RuntimeError):
    """Raised when required runtime configuration is missing."""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _kst_today_label(run_at_iso: str) -> str:
    """Return ``YYYY-MM-DD (KST)`` derived from the run's UTC timestamp."""

    try:
        dt = datetime.fromisoformat(run_at_iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


def _format_thread_title(payload: dict) -> str:
    date_label = _kst_today_label(payload.get("run_at", ""))
    errors = int(payload.get("seeds_with_errors", 0))
    accepted = int(payload.get("total_accepted", 0))
    seeds_total = int(payload.get("seeds_total", 0))
    seeds_skipped = int(payload.get("seeds_skipped_cooldown", 0))
    if errors:
        prefix = "🚨"
    elif seeds_total > 0 and seeds_skipped == seeds_total:
        # Healthy cooldown — every seed is within its 24h window. Avoid the ⚠️
        # alert emoji here so operators don't suffer alert fatigue on the most
        # common day-after-deploy state.
        prefix = "⏸️"
    elif accepted == 0:
        prefix = "⚠️"
    else:
        prefix = "🪨"
    title = f"{prefix} Miner Seeds {date_label} — accepted={accepted} errors={errors}"
    return title[:DISCORD_THREAD_TITLE_LIMIT]


def _format_thread_body(payload: dict) -> str:
    run_at = payload.get("run_at", "?")
    duration = payload.get("duration_sec", 0)
    seeds_total = payload.get("seeds_total", 0)
    seeds_processed = payload.get("seeds_processed", 0)
    seeds_skipped = payload.get("seeds_skipped_cooldown", 0)
    seeds_with_errors = payload.get("seeds_with_errors", 0)
    expanded = payload.get("total_expanded", 0)
    accepted = payload.get("total_accepted", 0)
    duplicate = payload.get("total_duplicate", 0)
    rejected = payload.get("total_rejected", 0)

    if seeds_with_errors:
        verdict = "❌ Some seeds failed — see details below."
    elif accepted == 0 and seeds_skipped == seeds_total:
        verdict = "⏸️ All seeds skipped (cooldown active). No new records this run."
    elif accepted == 0:
        verdict = "⚠️ Run finished without accepting any records. Check seed health."
    else:
        verdict = "✅ Run healthy."

    lines = [
        "**Run summary**",
        f"- run_at (UTC): `{run_at}`",
        f"- duration: `{duration}s`",
        f"- seeds: total=`{seeds_total}` processed=`{seeds_processed}` skipped_cooldown=`{seeds_skipped}` with_errors=`{seeds_with_errors}`",
        f"- records: expanded=`{expanded}` accepted=`{accepted}` duplicate=`{duplicate}` rejected=`{rejected}`",
        "",
        verdict,
        "",
        "**Per-seed**",
    ]

    for s in payload.get("summaries", []) or []:
        url = s.get("seed_url", "?")
        if s.get("error"):
            lines.append(f"- ❌ `{url}` — error: `{s.get('error')}`")
        elif s.get("skipped_cooldown"):
            lines.append(f"- ⏸️ `{url}` — cooldown skip")
        else:
            lines.append(
                f"- ✅ `{url}` — expanded={s.get('expanded_count', 0)} "
                f"accepted={s.get('accepted', 0)} dup={s.get('duplicate', 0)} "
                f"rej={s.get('rejected', 0)}"
            )

    intake = payload.get("intake_path")
    review = payload.get("review_queue_path")
    if intake or review:
        lines += ["", "**Paths (EC2)**"]
        if intake:
            lines.append(f"- intake: `{intake}`")
        if review:
            lines.append(f"- review queue: `{review}`")

    body = "\n".join(lines)
    if len(body) > DISCORD_MESSAGE_LIMIT:
        body = body[: DISCORD_MESSAGE_LIMIT - 3].rstrip() + "..."
    return body


async def _post_forum_thread(
    client: httpx.AsyncClient,
    *,
    token: str,
    channel_id: str,
    title: str,
    body: str,
) -> str:
    headers = {"Authorization": f"Bot {token}"}
    info = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
    info.raise_for_status()
    if int(info.json().get("type", 0)) != FORUM_CHANNEL_TYPE:
        raise ReportConfigError(
            f"DISCORD_OPS_REPORT_CHANNEL_ID={channel_id} is not a forum channel"
        )
    response = await client.post(
        f"https://discord.com/api/v10/channels/{channel_id}/threads",
        headers=headers,
        json={
            "name": title,
            "auto_archive_duration": 4320,
            "message": {
                "content": body,
                "allowed_mentions": {"parse": []},
            },
        },
    )
    response.raise_for_status()
    return str(response.json().get("id") or "")


async def run() -> None:
    _load_dotenv(Path.cwd() / ".env")

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise ReportConfigError("missing required env var: DISCORD_BOT_TOKEN")

    channel_id = os.environ.get("DISCORD_OPS_REPORT_CHANNEL_ID", DEFAULT_OPS_REPORT_CHANNEL_ID).strip()
    if not channel_id:
        raise ReportConfigError("DISCORD_OPS_REPORT_CHANNEL_ID is empty")

    status_path = Path(
        os.environ.get("MINER_SEEDS_STATUS_PATH", str(DEFAULT_STATUS_PATH))
    ).expanduser()
    if not status_path.exists():
        logger.warning("status file not found at %s — nothing to report", status_path)
        return

    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportConfigError(f"unable to read status at {status_path}: {exc}") from exc

    title = _format_thread_title(payload)
    body = _format_thread_body(payload)

    async with httpx.AsyncClient(timeout=30) as client:
        thread_id = await _post_forum_thread(
            client, token=token, channel_id=channel_id, title=title, body=body
        )

    logger.info(
        "posted miner-seeds report channel=%s thread=%s title=%r", channel_id, thread_id, title
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    try:
        asyncio.run(run())
    except ReportConfigError as exc:
        logger.error("configuration error: %s", exc)
        raise SystemExit(2) from exc
    except httpx.HTTPError as exc:
        logger.error("Discord HTTP error: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
