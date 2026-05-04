from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Briefing:
    title: str
    body: str
    source_path: Path


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    return text[end + 5 :]


def _plain_line(line: str) -> str:
    line = re.sub(r"[`*_]", "", line)
    line = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", line)
    return line.strip()


def _section(text: str, heading: str) -> str:
    pattern = rf"^##\s+{re.escape(heading)}\s*\n(?P<body>.*?)(?=\n##\s+|\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    return match.group("body").strip() if match else ""


def _first_paragraph(text: str) -> str:
    for part in re.split(r"\n\s*\n", text.strip()):
        cleaned = " ".join(_plain_line(line) for line in part.splitlines() if line.strip() and not line.strip().startswith("---"))
        if cleaned:
            return cleaned
    return ""


def render_briefing(source_path: Path, *, max_chars: int = 1800) -> Briefing:
    raw = source_path.expanduser().read_text()
    text = _strip_frontmatter(raw)
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    title = _plain_line(title_match.group(1)) if title_match else "집현전-Claw AI 브리핑"

    conclusion = _first_paragraph(_section(text, "1. 한 줄 결론"))
    trend_block = _section(text, "2. 핵심 동향 5가지")
    trends: list[str] = []
    for match in re.finditer(r"^###\s+\d+(?:\.\d+)?\s+(.+)$", trend_block, flags=re.MULTILINE):
        trends.append(_plain_line(match.group(1)))
        if len(trends) >= 5:
            break

    apply_block = _section(text, "3. 오늘의 OpenClaw 적용 제안")
    actions: list[str] = []
    for line in apply_block.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+", stripped):
            actions.append(_plain_line(stripped))
        if len(actions) >= 3:
            break

    lines = [
        "**집현전-Claw 업계 동향 브리핑**",
        f"_source: {source_path.name}_",
        "",
        f"**{title}**",
    ]
    if conclusion:
        lines += ["", f"**한 줄 결론**\n{conclusion}"]
    if trends:
        lines += ["", "**AI 업계 핵심 동향**"]
        lines += [f"- {item}" for item in trends]
    if actions:
        lines += ["", "**연구·제품 관점 시사점**"]
        lines += [f"- {item}" for item in actions]
    lines += ["", "자세한 업계 동향 원문은 PaperWiki의 AI Newsletter 페이지에 발행되어 있습니다."]

    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[: max(0, max_chars - 24)].rstrip() + "\n…(briefing truncated)"
    return Briefing(title=title, body=body, source_path=source_path)
