from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Briefing:
    title: str
    body: str
    source_path: Path


_DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


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
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                return stripped
            if isinstance(parsed, list):
                return _raw_string(parsed)
        return stripped
    if isinstance(value, list):
        return " ".join(_raw_string(item).rstrip(". ") + "." for item in value if _raw_string(item)).strip()
    return str(value).strip()


def _raw_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _shorten(value: object, limit: int) -> str:
    text = _plain_line(_raw_string(value))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip(" ,.;") + "…"


def _link_line(label: str, url: str) -> str:
    return f"↳ [{_shorten(label, 52)}]({url})"


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


def _paper_id_from_raw(paper: dict[str, object]) -> str:
    for key in ("paper_id", "id", "doi", "arxiv_id", "url"):
        value = _raw_string(paper.get(key))
        if value:
            return value
    return _raw_string(paper.get("title"))


def _candidate_index(raw: dict[str, object]) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    candidates = raw.get("candidates")
    if not isinstance(candidates, list):
        return out
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("paper_id", "id", "doi", "arxiv_id", "url", "title"):
            value = _raw_string(candidate.get(key))
            if value:
                out[value] = candidate
    return out


def _cluster_source_links(
    cluster: dict[str, object],
    candidates_by_id: dict[str, dict[str, object]],
    fallback_candidates: list[dict[str, object]],
    *,
    limit: int = 2,
) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    paper_ids = cluster.get("paper_ids")
    if isinstance(paper_ids, list):
        for paper_id in paper_ids:
            candidate = candidates_by_id.get(_raw_string(paper_id))
            if not candidate:
                continue
            link = _paper_link_from_raw(candidate)
            if link and link[1] not in seen:
                seen.add(link[1])
                links.append(link)
            if len(links) >= limit:
                return links
    for candidate in fallback_candidates:
        link = _paper_link_from_raw(candidate)
        if link and link[1] not in seen:
            seen.add(link[1])
            links.append(link)
        if len(links) >= limit:
            break
    return links


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
    title = "최신 연구 동향"
    if run_at:
        title = f"최신 연구 동향 — {run_at[:10]}"

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
        _DIVIDER,
        f"## {title}",
    ]

    conclusion = _plain_line(_raw_string(report_dict.get("at_a_glance")))
    if not conclusion:
        conclusion = _plain_line(_raw_string(report_dict.get("coverage_caveat")))
    if conclusion:
        if len(conclusion) > 420:
            conclusion = conclusion[:397].rstrip(" ,.") + "…"
        lines += ["", f"### 한 줄 결론\n{conclusion}"]

    candidates = raw.get("candidates")
    fallback_candidates = [c for c in candidates if isinstance(c, dict)] if isinstance(candidates, list) else []
    candidates_by_id = _candidate_index(raw)
    clusters = report_dict.get("clusters")
    rendered_clusters = 0
    if isinstance(clusters, list):
        lines += ["", f"{_DIVIDER}", "### 관련 최신 동향"]
        for cluster in clusters:
            if not isinstance(cluster, dict):
                continue
            title_text = _plain_line(_raw_string(cluster.get("title")))
            if not title_text:
                continue
            links = _cluster_source_links(cluster, candidates_by_id, fallback_candidates)
            source_lines = [_link_line(label, url) for label, url in links] or ["↳ 원문 raw.json 참고"]
            lines += [
                f"",
                f"### {rendered_clusters + 1}. {_shorten(title_text, 72)}",
                f"- **핵심 요약**: {_shorten(cluster.get('summary'), 155)}",
                f"- **기술 포인트**: {_shorten(cluster.get('why_it_matters'), 155)}",
                f"- **출처 링크**: {source_lines[0]}",
            ]
            lines.extend(source_lines[1:])
            rendered_clusters += 1
            if rendered_clusters >= 3:
                break

    lines += ["", _DIVIDER, "자세한 원문은 PaperWiki/weekly report에 보존되어 있습니다."]
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
    lines = [header, f"_source: {source_path.name}_"]
    if is_weekly_report:
        basis = "EC2 paper-recommender SOUL + 집현전 검색 evidence" if is_weekly_soul else "EC2 paper-recommender profile fallback + 집현전 검색 evidence"
        if soul_fallback:
            basis += " (SOUL fallback)"
        lines.append(f"_basis: {basis}_")
    lines += ["", _DIVIDER, f"## {title}"]
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
