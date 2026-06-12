from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from database.history import HistoryDB  # noqa: E402
from training import format_good_topics, good_topic_rows  # noqa: E402


def test_good_topics_prefers_approved_charging_topics(tmp_path):
    db_path = tmp_path / "history.db"
    db = HistoryDB(db_path)
    draft = {
        "id": "topic-1",
        "source": "CnEVPost",
        "headline": "China fast charging hub with battery swap",
        "images": ["photo.jpg"],
    }
    db.record_draft_created(draft)
    db.record_draft_approved("topic-1", "good", draft)

    rows = good_topic_rows(db_path, days=7)
    text = format_good_topics(rows, days=7)

    assert any(row["topic_tag"] in {"china_ev", "fast_charging", "charging_hub", "battery_swap"} for row in rows)
    assert "approved=1/1" in text
