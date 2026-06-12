from __future__ import annotations

import unittest
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (BASE_DIR / relative_path).read_text(encoding="utf-8", errors="ignore")


class StaticGuardTests(unittest.TestCase):
    def test_bot_has_global_error_handler(self) -> None:
        bot_py = read_text("bot.py")
        self.assertIn("async def error_handler", bot_py)
        self.assertIn("app.add_error_handler(error_handler)", bot_py)

    def test_monitor_reports_fatal_failures(self) -> None:
        monitor_py = read_text("monitor.py")
        self.assertIn("async def notify_monitor_failure", monitor_py)
        self.assertIn("Fatal monitor error in", monitor_py)

    def test_china_daily_report_refreshes_sources(self) -> None:
        monitor_py = read_text("monitor.py")
        self.assertIn("collect_china_candidates(24, force=True)", monitor_py)

    def test_normal_drafts_record_article_id(self) -> None:
        monitor_py = read_text("monitor.py")
        self.assertIn("record_draft_history(draft, aid)", monitor_py)

    def test_editorial_rules_keep_channel_voice(self) -> None:
        rules_md = read_text("channel_agent_rules.md")
        self.assertIn("Do not write for marketers", rules_md)
        self.assertIn("If the article is not actually about Russia", rules_md)


if __name__ == "__main__":
    unittest.main()
