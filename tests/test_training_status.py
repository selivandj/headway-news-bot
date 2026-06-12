from __future__ import annotations

import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from database.history import HistoryDB  # noqa: E402
from training import format_training_status, training_status  # noqa: E402


def test_training_status_counts_decisions(tmp_path, monkeypatch):
    now_ts = int(time.time())
    state_path = tmp_path / "training.env"
    state_path.write_text(
        "\n".join(
            [
                f"STARTED_AT={now_ts - 3600}",
                f"DEADLINE_AT={now_ts + 3600}",
                "RUNS=3",
                "TARGET_DRAFTS_PER_DAY=2",
            ]
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "history.db"
    db = HistoryDB(db_path)
    draft = {
        "id": "draft-1",
        "source": "TestSource",
        "headline": "fast charging hub",
        "created_at": now_ts - 100,
        "images": ["photo.jpg"],
    }
    db.record_draft_created(draft)
    db.record_draft_approved("draft-1", "ok", draft)
    db.record_feedback("draft-1", "rewrite_requested", "shorter", draft)
    db.record_bad_media("draft-1", "bad photo")

    monkeypatch.setattr("training.training_timer_active", lambda: True)
    monkeypatch.setattr("training.training_timer_enabled", lambda: True)

    status = training_status(db_path, state_path, now_ts=now_ts)

    assert status["enabled"] is True
    assert status["hours_left"] == 1.0
    assert status["runs"] == 3
    assert status["drafts_created"] == 1
    assert status["published"] == 1
    assert status["rewrite_requested"] == 1
    assert status["media_errors"] >= 1
    assert "Drafts created: 1" in format_training_status(status)
