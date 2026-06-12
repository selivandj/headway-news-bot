from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from security.redaction import redact_secrets  # noqa: E402


def test_redact_secrets_removes_full_secret_without_fragments():
    secret = "secret-value-abcdefghijklmnopqrstuvwxyz"
    text = f"request failed with token {secret}"

    redacted = redact_secrets(text, [secret])

    assert secret not in redacted
    assert secret[:6] not in redacted
    assert secret[-6:] not in redacted
    assert "[REDACTED]" in redacted


def test_redact_secrets_handles_empty_values():
    assert redact_secrets("plain text", ["", None]) == "plain text"
