from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT


def import_bot(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_REVIEW_CHAT_ID", "100")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@test_channel")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "100")
    monkeypatch.setenv("OWNER_CHAT_ID", "100")
    monkeypatch.setenv("ERROR_NOTIFICATIONS_ENABLED", "0")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    sys.path.insert(0, str(VPS_DIR))
    sys.modules.pop("bot", None)
    return importlib.import_module("bot")


def fake_update(chat_id: int, user_id: int):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(id=user_id),
        callback_query=None,
        message=None,
    )


def test_owner_guard_allows_owner(monkeypatch):
    bot = import_bot(monkeypatch)

    assert bot.is_owner(fake_update(100, 100)) is True


def test_owner_guard_blocks_other_user(monkeypatch):
    bot = import_bot(monkeypatch)

    assert bot.is_owner(fake_update(200, 200)) is False
