from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_env_example_contains_safe_stage_variables():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    required = (
        "ADMIN_TELEGRAM_ID=",
        "ERROR_NOTIFICATIONS_ENABLED=1",
        "DAILY_REPORT_ENABLED=1",
        "DAILY_REPORT_TIME=09:00",
        "SCHEDULER_ENABLED=1",
    )

    for item in required:
        assert item in text

    assert "ALLOW_WEB_IMAGE_SEARCH=0" in text
