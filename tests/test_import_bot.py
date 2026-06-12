from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT


def test_core_modules_import(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_REVIEW_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@test_channel")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "1")
    monkeypatch.setenv("ERROR_NOTIFICATIONS_ENABLED", "0")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("KIMI_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    sys.path.insert(0, str(VPS_DIR))
    try:
        for module_name in ("bot", "monitor", "database.history"):
            sys.modules.pop(module_name, None)
            module = importlib.import_module(module_name)
            assert module is not None
    finally:
        try:
            sys.path.remove(str(VPS_DIR))
        except ValueError:
            pass
