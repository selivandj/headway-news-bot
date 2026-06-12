from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from keyboards import confirm_publish_keyboard, draft_action_keyboard, draft_callback_id, reject_reason_keyboard  # noqa: E402


def flatten_callback_data(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def test_draft_keyboard_contains_draft_id():
    draft_id = "abc123draft"
    callbacks = flatten_callback_data(draft_action_keyboard(draft_id))

    assert f"publish:{draft_id}" in callbacks
    assert f"find_photo:{draft_id}" in callbacks
    assert f"generate_image:{draft_id}" in callbacks
    assert f"rewrite:{draft_id}" in callbacks
    assert f"reject:{draft_id}" in callbacks
    assert f"why:{draft_id}" in callbacks


def test_confirm_and_reject_keyboards_are_bound_to_draft():
    draft_id = draft_callback_id("123-abcdefabcdefabcdefabcdef.pending.json")

    confirm_callbacks = flatten_callback_data(confirm_publish_keyboard(draft_id))
    reject_callbacks = flatten_callback_data(reject_reason_keyboard(draft_id))

    assert f"confirm_publish:{draft_id}" in confirm_callbacks
    assert f"cancel:{draft_id}" in confirm_callbacks
    assert any(item.startswith(f"reject_reason:{draft_id}:") for item in reject_callbacks)
