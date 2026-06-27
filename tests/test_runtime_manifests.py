from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CHECK_PATH = ROOT / "scripts" / "check-runtime-manifests.py"
DEPLOY_PATH = ROOT / "scripts" / "deploy-openclaw-workspace.sh"
AUDIT_JOB_ID = "jiphyeonjeon-audit-team-digest"
AUDIT_AGENT_ID = "jiphyeonjeon-audit-team"
FORBIDDEN_AUDIT_COMMAND_SUBSTRINGS = (
    "systemctl start",
    "systemctl restart",
    "crontab",
    "install-cron",
    "install-newsletter",
    "install-card-news",
    "deploy",
    "post-card-news",
    "post-newsletter",
    "approve",
    "reject",
    " hold",
)
REQUIRED_AUDIT_BOUNDARY_PHRASES = (
    "does not approve",
    "does not mutate cron",
    "append-only",
    "never prints",
)


def load_checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_runtime_manifests", CHECK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def entries_by_id(checker: Any, manifest_text: str) -> dict[str, Any]:
    return {entry.id: entry for entry in checker._entries(manifest_text)}


class RuntimeManifestTest(unittest.TestCase):
    def test_runtime_manifests_validate(self) -> None:
        checker = load_checker()
        self.assertEqual([], checker.validate_runtime_manifests(ROOT))

    def test_deploy_gate_runs_runtime_manifest_check(self) -> None:
        deploy = DEPLOY_PATH.read_text()
        prompt_index = deploy.index("python3 scripts/check-prompt-governance.py")
        runtime_index = deploy.index("python3 scripts/check-runtime-manifests.py")
        self.assertLess(prompt_index, runtime_index)
        mkdir_index = deploy.index('"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p')
        self.assertLess(runtime_index, mkdir_index)
        self.assertIn("$REMOTE_WORKSPACE/skills", deploy[mkdir_index:])

    def test_required_phase_one_jobs_and_agents_are_declared(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text(encoding="utf-8")
        job_ids = {entry.id for entry in checker._entries(jobs_text)}
        agent_ids = {entry.id for entry in checker._entries(agents_text)}
        self.assertTrue(checker.REQUIRED_JOB_IDS.issubset(job_ids))
        self.assertTrue(checker.REQUIRED_AGENT_IDS.issubset(agent_ids))
        self.assertIn("jiphyeonjeon-editor-canonical-identity-report", job_ids)
        self.assertIn("jiphyeonjeon-advisor-evidence-quality-gate", job_ids)
        self.assertIn("jiphyeonjeon-editor", agent_ids)
        self.assertIn("jiphyeonjeon-advisor", agent_ids)
        self.assertNotIn("editorial-promotion-coordinator", job_ids)
        self.assertNotIn("editorial-promotion-coordinator", agent_ids)
        self.assertIn("집현전-편집자", agents_text)
        self.assertIn("집현전-지도교수", agents_text)
        self.assertIn("jiphyeonjeon-blog-publish", job_ids)
        self.assertIn("jiphyeonjeon-blog-publisher", agent_ids)
        self.assertIn("집현전-기자", agents_text)

    def test_audit_team_is_read_only_and_scoped(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text()
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text()
        jobs = entries_by_id(checker, jobs_text)
        agents = entries_by_id(checker, agents_text)
        job = jobs[AUDIT_JOB_ID]
        agent = agents[AUDIT_AGENT_ID]
        self.assertEqual(AUDIT_AGENT_ID, job.fields["owner_agent"])
        self.assertIn("health-check", job.fields["type"])
        command_refs = " ".join(job.lists["command_refs"]).lower()
        self.assertIn("discord-openclaw-audit-team", command_refs)
        for forbidden in FORBIDDEN_AUDIT_COMMAND_SUBSTRINGS:
            self.assertNotIn(forbidden, command_refs)
        boundaries = " ".join(agent.lists["boundaries"]).lower()
        for required in REQUIRED_AUDIT_BOUNDARY_PHRASES:
            self.assertIn(required, boundaries)
        # The negative command gate is intentionally scoped to the audit job;
        # existing non-audit runtime jobs may legitimately install cron or post.
        all_non_audit_commands = " ".join(
            " ".join(entry.lists.get("command_refs", []))
            for entry in jobs.values()
            if entry.id != AUDIT_JOB_ID
        ).lower()
        self.assertIn("install-newsletter-archive-cron.sh", all_non_audit_commands)
        self.assertIn("post-card-news.sh", all_non_audit_commands)

    def test_runtime_checker_rejects_forbidden_audit_command_refs(self) -> None:
        checker = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "runtime", tmp_root / "runtime")
            jobs_path = tmp_root / "runtime" / "jobs.yaml"
            jobs_path.write_text(
                jobs_path.read_text(encoding="utf-8").replace(
                    "uv run discord-openclaw-audit-team\n",
                    "uv run discord-openclaw-audit-team\n      - systemctl restart discord-openclaw-bridge.service\n",
                ),
                encoding="utf-8",
            )
            errors = checker.validate_runtime_manifests(tmp_root)
        self.assertTrue(any("audit command_refs" in error for error in errors), errors)

    def test_editor_and_advisor_are_advisory_only_without_promotion_activation(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text()
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text()
        jobs = {entry.id: entry for entry in checker._entries(jobs_text)}
        agents = {entry.id: entry for entry in checker._entries(agents_text)}
        for job_id in ("jiphyeonjeon-editor-canonical-identity-report", "jiphyeonjeon-advisor-evidence-quality-gate"):
            job = jobs[job_id]
            self.assertIn("local-advisory", job.fields["type"])
            command_refs = " ".join(job.lists["command_refs"])
            self.assertNotIn("discord", command_refs.lower())
            self.assertNotIn("systemctl", command_refs.lower())
            self.assertNotIn("cron", command_refs.lower())
        self.assertNotIn("editorial-promotion-coordinator", jobs)
        self.assertNotIn("editorial-promotion-coordinator", agents)
        for job in jobs.values():
            self.assertNotEqual("editorial-promotion-coordinator", job.fields.get("owner_agent", ""))
            self.assertNotIn("editorial-promotion-coordinator", " ".join(job.lists.get("command_refs", [])))
        for agent in agents.values():
            self.assertNotIn("editorial-promotion-coordinator", agent.lists.get("owns_jobs", []))


    def test_blog_publisher_is_dry_run_guarded_and_non_destructive(self) -> None:
        checker = load_checker()
        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")
        agents_text = (ROOT / "runtime" / "agents.yaml").read_text(encoding="utf-8")
        jobs = entries_by_id(checker, jobs_text)
        agents = entries_by_id(checker, agents_text)
        job = jobs["jiphyeonjeon-blog-publish"]
        agent = agents["jiphyeonjeon-blog-publisher"]
        command_refs = " ".join(job.lists.get("command_refs", [])).lower()
        safety = " ".join([*job.fields.values(), *job.lists.get("pre_publish_checks", []), *job.lists.get("outputs", [])]).lower()
        boundaries = " ".join(agent.lists.get("boundaries", [])).lower()
        self.assertIn("post-blog.sh", command_refs)
        self.assertIn("discord-openclaw-post-blog", command_refs)
        self.assertIn("--dry-run", command_refs)
        self.assertIn("--publish", command_refs)
        self.assertIn(".codex/skills/jiphyeonjeon-reporter-article-post/SKILL.md", agent.lists.get("source_refs", []))
        self.assertIn("approval", safety + boundaries)
        self.assertIn("advisory", safety + boundaries)
        self.assertIn("dry-run", safety + boundaries)
        self.assertIn("no delete", safety + boundaries)
        self.assertNotIn("editorial-promotion-coordinator", command_refs)
        self.assertNotIn(" delete", command_refs)
        self.assertNotIn("/delete", command_refs)

    def test_traveler_runner_uses_runtime_scout_topics(self) -> None:
        runner = ROOT / "skills" / "discord-openclaw-bridge" / "project" / "scripts" / "run-traveler-collection-report.sh"
        installer = ROOT / "skills" / "discord-openclaw-bridge" / "install-traveler-collection-report-cron.sh"
        stable_entrypoint = ROOT / "scripts" / "traveler-collection-report.sh"

        runner_text = runner.read_text(encoding="utf-8")
        wrapper_text = stable_entrypoint.read_text(encoding="utf-8")
        self.assertIn("runtime/traveler-scout-topics.json", runner_text)
        self.assertIn("HERMES_WORKSPACE", runner_text)
        self.assertIn("JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH", runner_text)
        self.assertIn("${JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH}", runner_text)
        self.assertIn("scripts/paperwiki_kg.py", runner_text)
        self.assertIn("scout-topics", runner_text)
        self.assertIn("mktemp", runner_text)
        self.assertIn("traveler-scout-topics.paperwiki.json", runner_text)
        self.assertIn("JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE", runner_text)
        self.assertIn("JIPHYEONJEON_TRAVELER_TOPICS_GENERATED_FROM", runner_text)
        self.assertIn("JIPHYEONJEON_TRAVELER_ENABLE_PAPERWIKI_KG", runner_text)
        self.assertIn("JIPHYEONJEON_TRAVELER_SCOUT_MAX_TOPICS", runner_text)
        self.assertIn("--max-topics", runner_text)
        self.assertIn("paperwiki_interests_used", runner_text)
        self.assertIn("PaperWiki KG merge failed; using baseline topics", runner_text)
        self.assertIn("HERMES_WORKSPACE", wrapper_text)
        self.assertIn("runtime/traveler-scout-topics.json", installer.read_text(encoding="utf-8"))
        self.assertTrue(stable_entrypoint.exists())
        self.assertIn("run-traveler-collection-report.sh", wrapper_text)
        installer_text = installer.read_text(encoding="utf-8")
        self.assertIn("scripts/traveler-collection-report.sh", installer_text)
        self.assertIn("HERMES_WORKSPACE=$WORKSPACE $WRAPPER", installer_text)
        self.assertIn("bash -n \"$WRAPPER\"", installer_text)
        self.assertIn("bash -n \"$RUNNER\"", installer_text)

    def test_traveler_runtime_manifests_declare_optional_paperwiki_kg(self) -> None:
        jobs = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")
        agents = (ROOT / "runtime" / "agents.yaml").read_text(encoding="utf-8")

        self.assertIn("python3 scripts/paperwiki_kg.py scout-topics --base runtime/traveler-scout-topics.json", jobs)
        self.assertIn("PAPERWIKI_KG_DB", jobs)
        self.assertIn("optional PaperWiki KG DB path PAPERWIKI_KG_DB", jobs)
        self.assertIn("missing or unhealthy KG falls back to committed scout topics", jobs)
        self.assertIn("never bypasses evidence collection or Claw review", jobs)
        self.assertIn("scripts/paperwiki_kg.py", agents)
        self.assertIn("optionally use PaperWiki KG active interests", agents)
        self.assertIn("PaperWiki KG as advisory only", agents)

    def test_traveler_cron_installer_rejects_unsafe_workspace(self) -> None:
        installer = ROOT / "skills" / "discord-openclaw-bridge" / "install-traveler-collection-report-cron.sh"
        for workspace in ("~/.hermes/work space", "~/.hermes/work%space"):
            with self.subTest(workspace=workspace):
                result = subprocess.run(
                    ["bash", str(installer)],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "REMOTE_HOST": "example.invalid",
                        "KEY_FILE": "/tmp/nonexistent-jiphyeonjeon-key",
                        "REMOTE_WORKSPACE": workspace,
                    },
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(2, result.returncode)
                self.assertIn("unsafe shell characters", result.stderr)

    def test_newsletter_cron_uses_committed_runners(self) -> None:
        skill_root = ROOT / "skills" / "discord-openclaw-bridge"
        newsletter_runner = skill_root / "project" / "scripts" / "run-newsletter-archive-and-cardnews.sh"
        briefing_runner = skill_root / "project" / "scripts" / "run-daily-jiphyeonjeon-briefing.sh"
        installer = skill_root / "install-newsletter-archive-cron.sh"
        stable_newsletter_entrypoint = ROOT / "scripts" / "newsletter-archive-and-cardnews.sh"
        stable_briefing_entrypoint = ROOT / "scripts" / "daily-jiphyeonjeon-briefing.sh"

        self.assertTrue(newsletter_runner.exists())
        self.assertTrue(briefing_runner.exists())
        self.assertTrue(stable_newsletter_entrypoint.exists())
        self.assertTrue(stable_briefing_entrypoint.exists())
        self.assertIn("run-newsletter-archive-and-cardnews.sh", stable_newsletter_entrypoint.read_text(encoding="utf-8"))
        briefing_text = briefing_runner.read_text(encoding="utf-8")
        stable_newsletter_text = stable_newsletter_entrypoint.read_text(encoding="utf-8")
        stable_briefing_text = stable_briefing_entrypoint.read_text(encoding="utf-8")
        self.assertIn("run-daily-jiphyeonjeon-briefing.sh", stable_briefing_text)
        self.assertIn("HERMES_WORKSPACE", stable_newsletter_text)
        self.assertIn("HERMES_WORKSPACE", stable_briefing_text)
        self.assertIn("HERMES_WORKSPACE", (newsletter_runner.read_text(encoding="utf-8")))
        self.assertIn("HERMES_WORKSPACE", briefing_text)
        self.assertNotIn("weekly-report --force", briefing_text)
        self.assertIn("newsletter-archive-briefing.sh", briefing_text)
        self.assertIn("NEWSLETTER_DATE", briefing_text)
        self.assertIn("NEWSLETTER_ARCHIVE_SOURCE", briefing_text)
        self.assertIn("DAILY_BRIEFING_WAIT_SECONDS", briefing_text)
        self.assertIn("NEWSLETTER_ARCHIVE_LOCK_DIR", briefing_text)
        self.assertIn("wait_for_archive_runner", briefing_text)
        self.assertIn('grep -Fqx -- "작성일: \`$NEWSLETTER_DATE\`"', briefing_text)
        self.assertIn('RUN_DATE="${NEWSLETTER_DATE:-$(date +%F)}"', briefing_text)
        self.assertIn("집현전 데일리 뉴스레터 — 기술 블로그 브리핑", briefing_text)
        self.assertIn("오늘의 핵심 항목 — 기술 블로그형 소개", briefing_text)
        self.assertIn("기술 소개 → 왜 중요한가 → 실무/연구 포인트 → 원문", briefing_text)
        self.assertIn("technical_intro", briefing_text)
        self.assertIn("context_note", briefing_text)
        self.assertIn("practice_note", briefing_text)
        self.assertIn("canonical_url", briefing_text)
        self.assertIn("normalized_title", briefing_text)
        self.assertIn("semantic_family", briefing_text)
        self.assertIn("GRAPH_EMBEDDING_FAMILY_RE", briefing_text)
        self.assertIn("display_title", briefing_text)
        self.assertIn("dated_items", briefing_text)
        self.assertIn("이전 날짜 제외", briefing_text)
        self.assertIn("received_at/published_at", briefing_text)
        self.assertIn("주제 기준 핵심 묶음", briefing_text)
        self.assertIn("daily-trends-latest.md", briefing_text)
        installer_text = installer.read_text(encoding="utf-8")
        self.assertIn("run-newsletter-archive-and-cardnews.sh", installer_text)
        self.assertIn("run-daily-jiphyeonjeon-briefing.sh", installer_text)
        self.assertIn("rsync -az", installer_text)
        self.assertIn("bash -n \"$NEWSLETTER_RUNNER\"", installer_text)

        jobs_text = (ROOT / "runtime" / "jobs.yaml").read_text(encoding="utf-8")
        self.assertIn("install-newsletter-archive-cron.sh", jobs_text)
        self.assertIn("run-newsletter-archive-and-cardnews.sh", jobs_text)


    def test_daily_briefing_runner_collapses_graph_embedding_family(self) -> None:
        runner = ROOT / "skills" / "discord-openclaw-bridge" / "project" / "scripts" / "run-daily-jiphyeonjeon-briefing.sh"
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            builder = workspace / "skills" / "paper-recommender" / "scripts" / "newsletter-archive-briefing.sh"
            publisher = workspace / "skills" / "discord-openclaw-bridge" / "project" / ".venv" / "bin" / "discord-openclaw-post-briefing"
            builder.parent.mkdir(parents=True)
            publisher.parent.mkdir(parents=True)
            builder.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$(dirname "$NEWSLETTER_REPORT_PATH")" "$(dirname "$NEWSLETTER_ARCHIVE_SOURCE")"
cat > "$NEWSLETTER_ARCHIVE_SOURCE" <<JSON
{"date":"$NEWSLETTER_DATE","items":[{"title":"Dynamic graph embedding survey","public_excerpt":"Dynamic graph embedding methods","url":"https://example.com/dyn","source":"A","received_at":"2026-05-23 01:00"},{"title":"Graph representation learning benchmarks","public_excerpt":"Graph representation learning benchmark set","url":"https://example.com/canonical","source":"B","received_at":"2026-05-23 02:00"},{"title":"Heterogeneous graph embedding benchmark","public_excerpt":"Heterogeneous graph embedding methods","url":"https://example.com/het","source":"C","received_at":"2026-05-23 03:00"},{"title":"RAG evaluation benchmark","public_excerpt":"Distinct retrieval benchmark","url":"https://example.com/rag","source":"D","received_at":"2026-05-23 04:00"},{"title":"Old repeated Anthropic item","public_excerpt":"Old item must not appear","url":"https://example.com/old","source":"E","received_at":"2026-05-21 04:22"}]}
JSON
printf '**집현전-Claw 기술 블로그 브리핑**\n작성일: `%s`\n' "$NEWSLETTER_DATE" > "$NEWSLETTER_REPORT_PATH"
""",
                encoding="utf-8",
            )
            publisher.write_text("#!/usr/bin/env bash\nset -euo pipefail\ncat \"$DISCORD_BRIEFING_SOURCE\" >/dev/null\n", encoding="utf-8")
            builder.chmod(0o755)
            publisher.chmod(0o755)

            env = os.environ.copy()
            env.update({"OPENCLAW_WORKSPACE": str(workspace), "NEWSLETTER_DATE": "2026-05-23", "DAILY_BRIEFING_WAIT_SECONDS": "0"})
            subprocess.run(["bash", str(runner)], env=env, check=True, cwd=ROOT)

            rendered = (workspace / "reports" / "daily-trends-latest.md").read_text(encoding="utf-8")
            lower = rendered.lower()
            self.assertIn("전체 5개 / 당일 4개 / 이전 날짜 제외 1개 / 주제 기준 핵심 묶음 2개", rendered)
            self.assertIn("집현전 데일리 뉴스레터 — 기술 블로그 브리핑", rendered)
            self.assertIn("## 읽는 법", rendered)
            self.assertIn("기술 소개 → 왜 중요한가 → 실무/연구 포인트 → 원문", rendered)
            self.assertIn("- 기술 소개:", rendered)
            self.assertIn("- 실무/연구 포인트:", rendered)
            self.assertIn("- 원문 링크:", rendered)
            self.assertIn("그래프 표현학습 최신 벤치마크 묶음", rendered)
            self.assertIn("관련 링크: 같은 제목으로 수집된 추가 공개 링크 2개", rendered)
            self.assertIn("RAG evaluation benchmark", rendered)
            self.assertNotIn("Old repeated Anthropic item", rendered)
            self.assertNotIn("Old item must not appear", rendered)
            self.assertEqual(2, rendered.count("### "))
            self.assertNotIn("dynamic graph embedding", lower)
            self.assertNotIn("heterogeneous graph embedding", lower)
            self.assertNotIn("graph representation learning benchmarks", lower)

    def test_malformed_yaml_is_rejected_when_yaml_parser_available(self) -> None:
        if shutil.which("ruby") is None:
            self.skipTest("Ruby YAML parser unavailable in this environment")
        checker = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            shutil.copytree(ROOT / "runtime", tmp_root / "runtime")
            jobs_path = tmp_root / "runtime" / "jobs.yaml"
            jobs_path.write_text(jobs_path.read_text() + "\ninvalid_flow: [unterminated\n")
            errors = checker.validate_runtime_manifests(tmp_root)
        self.assertTrue(any("invalid YAML syntax" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
