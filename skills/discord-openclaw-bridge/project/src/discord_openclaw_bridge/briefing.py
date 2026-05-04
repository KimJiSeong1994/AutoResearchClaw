from __future__ import annotations

import json
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


def _frontmatter_value(raw: str, key: str) -> str | None:
    if not raw.startswith("---\n"):
        return None
    end = raw.find("\n---\n", 4)
    if end < 0:
        return None
    frontmatter = raw[4:end]
    match = re.search(rf"^{re.escape(key)}:\s*\"?(.*?)\"?\s*$", frontmatter, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _source_links(text: str, *, limit: int = 5) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "http" not in line:
            continue
        label = _plain_line(line).lstrip("- ").strip()
        for url in re.findall(r"https?://[^\s)]+", line):
            clean_url = url.rstrip(".,;]")
            if clean_url in seen:
                continue
            seen.add(clean_url)
            source_label = label
            if ": http" in source_label:
                source_label = source_label.split(": http", 1)[0].strip()
            source_label = source_label[:80] or clean_url
            links.append((source_label, clean_url))
            if len(links) >= limit:
                return links
    return links




def _raw_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _raw_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _paper_link_from_raw(paper: dict[str, object]) -> tuple[str, str] | None:
    title = _plain_line(_raw_string(paper.get("title"))) or _raw_string(paper.get("paper_id")) or "paper"
    for key in ("url", "pdf_url"):
        url = _raw_string(paper.get(key))
        if url.startswith(("https://", "http://")):
            return (title[:80], url)
    arxiv_id = _raw_string(paper.get("arxiv_id"))
    if re.match(r"^\d{4}\.\d{4,5}(?:v\d+)?$", arxiv_id):
        return (title[:80], f"https://arxiv.org/abs/{arxiv_id}")
    return None


def _adjacent_raw_path(source_path: Path) -> Path:
    return source_path.expanduser().parent / "raw.json"


def _load_adjacent_weekly_raw(source_path: Path) -> dict[str, object] | None:
    raw_path = _adjacent_raw_path(source_path)
    if not raw_path.exists():
        return None
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    report = data.get("report")
    if not isinstance(report, dict):
        return None
    if "clusters" not in report and "at_a_glance" not in report:
        return None
    return data


def _render_weekly_raw_briefing(source_path: Path, raw: dict[str, object], *, max_chars: int) -> Briefing:
    report = raw.get("report") if isinstance(raw.get("report"), dict) else {}
    report_dict = report if isinstance(report, dict) else {}
    run_at = _raw_string(raw.get("run_at"))
    title = "Weekly research trends"
    if run_at:
        title = f"Weekly research trends — {run_at[:10]}"

    soul_source = _raw_string(raw.get("soul_source"))
    soul_fallback = _raw_bool(raw.get("soul_fallback_used"))
    is_weekly_soul = soul_source == "soul"
    if is_weekly_soul:
        header = "**집현전-Claw 관심논문·SOUL 기반 연구 동향 브리핑**"
    else:
        header = "**집현전-Claw 관심논문·Profile Fallback 연구 동향 브리핑**"

    basis = "EC2 paper-recommender SOUL + 집현전 검색 evidence" if is_weekly_soul else "EC2 paper-recommender profile fallback + 집현전 검색 evidence"
    if soul_fallback:
        basis += " (SOUL fallback)"
    if soul_source and not is_weekly_soul:
        basis += f"; source={soul_source.replace(chr(10), ' ').replace(chr(13), ' ')}"

    lines = [
        header,
        f"_source: {source_path.name} + raw.json_",
        f"_basis: {basis}_",
        "",
        f"**{title}**",
    ]

    conclusion = _plain_line(_raw_string(report_dict.get("at_a_glance")))
    if not conclusion:
        conclusion = _plain_line(_raw_string(report_dict.get("coverage_caveat")))
    if conclusion:
        if len(conclusion) > 420:
            conclusion = conclusion[:397].rstrip(" ,.") + "…"
        lines += ["", f"**한 줄 결론**\n{conclusion}"]

    trends: list[str] = []
    clusters = report_dict.get("clusters")
    if isinstance(clusters, list):
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            title_text = _plain_line(_raw_string(cluster.get("title")))
            if title_text:
                trends.append(title_text)
            if len(trends) >= 4:
                break
    if trends:
        lines += ["", "**SOUL/Profile 기반 핵심 연구·기술 클러스터**"]
        lines += [f"- {item}" for item in trends]

    candidates = raw.get("candidates")
    source_links: list[tuple[str, str]] = []
    actions: list[str] = []
    if isinstance(candidates, list):
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            link = _paper_link_from_raw(candidate)
            if link and link[1] not in seen:
                seen.add(link[1])
                source_links.append(link)
            title_text = _plain_line(_raw_string(candidate.get("title")))
            trend_query = _plain_line(_raw_string(candidate.get("trend_query")))
            if title_text:
                actions.append(title_text if not trend_query else f"{title_text} — {trend_query}")
            if len(source_links) >= 5 and len(actions) >= 3:
                break

    if source_links:
        lines += ["", "**출처 링크**"]
        lines += [f"- {label}: {url}" for label, url in source_links[:4]]
    if actions:
        lines += ["", "**우선 읽을 논문/근거**"]
        lines += [f"- {item.split(' — ', 1)[0]}" for item in actions[:2]]

    lines += ["", "자세한 SOUL/Profile 기반 연구 동향 원문은 PaperWiki/weekly report에 보존되어 있습니다."]
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[: max(0, max_chars - 24)].rstrip() + "\n…(briefing truncated)"
    return Briefing(title=title, body=body, source_path=source_path)


def render_briefing(source_path: Path, *, max_chars: int = 1800) -> Briefing:
    weekly_raw = _load_adjacent_weekly_raw(source_path)
    if weekly_raw is not None:
        return _render_weekly_raw_briefing(source_path, weekly_raw, max_chars=max_chars)

    raw = source_path.expanduser().read_text(encoding="utf-8")
    text = _strip_frontmatter(raw)
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    title = _plain_line(title_match.group(1)) if title_match else "집현전-Claw AI 브리핑"

    report_type = _frontmatter_value(raw, "report_type") or ""
    soul_source = _frontmatter_value(raw, "soul_source") or ""
    soul_fallback = (_frontmatter_value(raw, "soul_fallback_used") or "").lower() == "true"
    is_weekly_report = report_type in {"weekly-soul-trends", "weekly-profile-trends"} or "# Weekly research trends" in text
    is_weekly_soul = report_type == "weekly-soul-trends" or (is_weekly_report and soul_source == "soul")

    conclusion = _first_paragraph(_section(text, "1. 한 줄 결론"))
    if not conclusion and is_weekly_report:
        conclusion = _first_paragraph(_section(text, "At a glance"))

    trend_block = _section(text, "2. 핵심 동향 5가지")
    if not trend_block and is_weekly_report:
        trend_block = _section(text, "Trend clusters")
    trends: list[str] = []
    for match in re.finditer(r"^###\s+(?:\d+(?:\.\d+)?\.?\s+)?(.+)$", trend_block, flags=re.MULTILINE):
        trends.append(_plain_line(match.group(1)))
        if len(trends) >= 4:
            break

    source_links = _source_links(text, limit=5)

    apply_block = _section(text, "3. 오늘의 OpenClaw 적용 제안")
    actions: list[str] = []
    for line in apply_block.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+\.\s+", stripped):
            actions.append(_plain_line(stripped))
        if len(actions) >= 3:
            break
    if not actions and is_weekly_report:
        reading = _section(text, "Reading queue")
        for line in reading.splitlines():
            stripped = line.strip()
            if re.match(r"^\d+\.\s+", stripped):
                actions.append(_plain_line(stripped))
            if len(actions) >= 3:
                break

    if is_weekly_soul:
        header = "**집현전-Claw 관심논문·SOUL 기반 연구 동향 브리핑**"
    elif is_weekly_report:
        header = "**집현전-Claw 관심논문·Profile Fallback 연구 동향 브리핑**"
    else:
        header = "**집현전-Claw 업계 동향 브리핑**"
    lines = [
        header,
        f"_source: {source_path.name}_",
    ]
    if is_weekly_report:
        basis = "EC2 paper-recommender SOUL + 집현전 검색 evidence" if is_weekly_soul else "EC2 paper-recommender profile fallback + 집현전 검색 evidence"
        if soul_fallback:
            basis += " (SOUL fallback)"
        lines.append(f"_basis: {basis}_")
    lines += [
        "",
        f"**{title}**",
    ]
    if conclusion:
        if is_weekly_report and len(conclusion) > 420:
            conclusion = conclusion[:397].rstrip(" ,.") + "…"
        lines += ["", f"**한 줄 결론**\n{conclusion}"]
    if trends:
        trend_heading = "**SOUL/Profile 기반 핵심 연구·기술 클러스터**" if is_weekly_report else "**AI 업계 핵심 동향**"
        lines += ["", trend_heading]
        lines += [f"- {item}" for item in trends]
    if source_links:
        lines += ["", "**출처 링크**"]
        lines += [f"- {label}: {url}" for label, url in source_links[:4]]
    if actions:
        action_heading = "**우선 읽을 논문/근거**" if is_weekly_report else "**연구·제품 관점 시사점**"
        lines += ["", action_heading]
        shown_actions = actions[:2] if is_weekly_report else actions
        if is_weekly_report:
            shown_actions = [a.split(" — ", 1)[0] for a in shown_actions]
        lines += [f"- {item}" for item in shown_actions]
    tail = "자세한 SOUL/Profile 기반 연구 동향 원문은 PaperWiki/weekly report에 보존되어 있습니다." if is_weekly_report else "자세한 업계 동향 원문은 PaperWiki의 AI Newsletter 페이지에 발행되어 있습니다."
    lines += ["", tail]

    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[: max(0, max_chars - 24)].rstrip() + "\n…(briefing truncated)"
    return Briefing(title=title, body=body, source_path=source_path)
