from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "autoresearch_pdf_fetch.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("autoresearch_pdf_fetch", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules["autoresearch_pdf_fetch"] = mod
spec.loader.exec_module(mod)


def test_items_from_papers_md_parses_representative_bullet(tmp_path: Path) -> None:
    # A representative bullet in the format produced by daily_research._render_paper_bullet
    bullet = (
        "- **Attention Is All You Need**  _(arxiv · 2017 · NeurIPS)_\n"
        "  - Vaswani et al.\n"
        "  - [https://arxiv.org/abs/1706.03762](https://arxiv.org/abs/1706.03762)\n"
        "  - arxiv: `1706.03762`\n"
        "  - _Transformer architecture replacing recurrence with self-attention._\n"
    )
    date_dir = tmp_path / "2026-05-01"
    date_dir.mkdir()
    (date_dir / "daily-research-papers.md").write_text(bullet, encoding="utf-8")

    items = mod.items_from_papers_md(date_dir)

    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Attention Is All You Need"
    assert item["source"] == "arxiv"
    assert item["year"] == 2017
    assert item["arxiv_id"] == "1706.03762"
    assert item["url"] == "https://arxiv.org/abs/1706.03762"
    assert item["abstract"] == "Transformer architecture replacing recurrence with self-attention."
    # venue and authors are projected out
    assert "venue" not in item
    assert "authors" not in item
