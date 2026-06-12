from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from database.history import HistoryDB  # noqa: E402
from training import AUTO_END, AUTO_START, build_editorial_memory_auto_block, update_editorial_memory_file  # noqa: E402


def test_editorial_memory_update_preserves_manual_part(tmp_path):
    db_path = tmp_path / "history.db"
    db = HistoryDB(db_path)
    draft = {
        "id": "memory-1",
        "source": "GoodSource",
        "headline": "fast charging station",
        "images": ["photo.jpg"],
    }
    db.record_draft_created(draft)
    db.record_draft_approved("memory-1", "publish", draft)

    memory_path = tmp_path / "editorial_memory.md"
    memory_path.write_text("# Manual Memory\n\nKeep this note.\n", encoding="utf-8")
    block = build_editorial_memory_auto_block(db_path, days=90)
    result = update_editorial_memory_file(memory_path, block)
    text = memory_path.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert AUTO_START in text and AUTO_END in text
    assert "GoodSource" in text
    assert "Keep this note." in text


def test_editorial_memory_update_replaces_only_auto_block(tmp_path):
    memory_path = tmp_path / "editorial_memory.md"
    memory_path.write_text(
        f"before\n{AUTO_START}\nold\n{AUTO_END}\nafter\n",
        encoding="utf-8",
    )
    update_editorial_memory_file(memory_path, f"{AUTO_START}\nnew\n{AUTO_END}\n")

    text = memory_path.read_text(encoding="utf-8")
    assert "before" in text
    assert "after" in text
    assert "new" in text
    assert "old" not in text
