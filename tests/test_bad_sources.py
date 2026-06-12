from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from database.history import HistoryDB  # noqa: E402
from training import bad_source_rows, format_bad_sources  # noqa: E402


def test_bad_sources_detects_low_approval_and_media_problems(tmp_path):
    db_path = tmp_path / "history.db"
    db = HistoryDB(db_path)

    for index in range(3):
        draft = {
            "id": f"noise-{index}",
            "source": "NoiseFeed",
            "headline": "not about charging",
        }
        db.record_draft_created(draft)
        db.record_draft_rejected(draft["id"], "не про зарядную инфраструктуру", draft)
        db.record_bad_media(draft["id"], "плохое медиа")

    rows = bad_source_rows(db_path, days=7)

    assert rows
    assert rows[0]["source_name"] == "NoiseFeed"
    assert rows[0]["approval_rate"] == 0.0
    assert "Источник можно отключить" in format_bad_sources(rows, days=7)
