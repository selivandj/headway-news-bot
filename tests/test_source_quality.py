from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from database.history import HistoryDB  # noqa: E402
from training import format_source_quality, source_quality_rows  # noqa: E402


def test_source_quality_recommends_keep_watch_disable(tmp_path):
    db_path = tmp_path / "history.db"
    db = HistoryDB(db_path)

    for index in range(3):
        draft = {
            "id": f"good-{index}",
            "source": "GoodSource",
            "headline": "EV charging station fast charging",
            "images": ["ok.jpg"],
        }
        db.record_draft_created(draft)
        db.record_draft_approved(draft["id"], "published", draft)

    for index in range(3):
        draft = {
            "id": f"bad-{index}",
            "source": "BadSource",
            "headline": "generic green agenda",
        }
        db.record_draft_created(draft)
        db.record_draft_rejected(draft["id"], "weak news", draft)

    rows = source_quality_rows(db_path, days=7)
    by_source = {row["source_name"]: row for row in rows}

    assert by_source["GoodSource"]["recommendation"] == "keep"
    assert by_source["BadSource"]["recommendation"] == "disable"
    assert "GoodSource" in format_source_quality(rows, days=7)
