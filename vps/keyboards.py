from __future__ import annotations

import hashlib
import re
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def draft_callback_id(draft_path_or_id: str | Path | None) -> str:
    """Return a short stable id that fits Telegram callback_data limits."""
    value = str(draft_path_or_id or "draft")
    name = Path(value).name
    match = re.match(r"^\d+-([0-9a-f]{24})(?:[-.]|$)", name)
    if match:
        return match.group(1)
    stem = Path(name).stem or value
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_")
    if safe and len(safe) <= 28:
        return safe
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def draft_action_keyboard(draft_id: str | Path) -> InlineKeyboardMarkup:
    did = draft_callback_id(draft_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Публиковать", callback_data=f"publish:{did}"),
                InlineKeyboardButton("🖼 Найти фото", callback_data=f"find_photo:{did}"),
            ],
            [
                InlineKeyboardButton("🎨 Сгенерировать", callback_data=f"generate_image:{did}"),
                InlineKeyboardButton("✏️ Переделать", callback_data=f"rewrite:{did}"),
            ],
            [
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject:{did}"),
                InlineKeyboardButton("ℹ️ Почему подходит?", callback_data=f"why:{did}"),
            ],
        ]
    )


def confirm_publish_keyboard(draft_id: str | Path) -> InlineKeyboardMarkup:
    did = draft_callback_id(draft_id)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да, публикуй", callback_data=f"confirm_publish:{did}"),
                InlineKeyboardButton("Отмена", callback_data=f"cancel:{did}"),
            ]
        ]
    )


REJECT_REASONS = {
    "no_facts": "Нет цифр / фактуры",
    "off_topic": "Не про зарядную инфраструктуру",
    "weak_news": "Слабая новость",
    "bad_text": "Плохой текст",
    "bad_media": "Плохое медиа",
    "other": "Другая причина",
}


def reject_reason_keyboard(draft_id: str | Path) -> InlineKeyboardMarkup:
    did = draft_callback_id(draft_id)
    rows = []
    items = list(REJECT_REASONS.items())
    for index in range(0, len(items), 2):
        rows.append(
            [
                InlineKeyboardButton(label, callback_data=f"reject_reason:{did}:{code}")
                for code, label in items[index : index + 2]
            ]
        )
    rows.append([InlineKeyboardButton("Отмена", callback_data=f"cancel:{did}")])
    return InlineKeyboardMarkup(rows)


def reject_reason_label(code: str) -> str:
    return REJECT_REASONS.get(code, code or "Другая причина")
