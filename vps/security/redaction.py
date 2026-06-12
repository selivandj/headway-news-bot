from __future__ import annotations


def redact_secrets(value: str, secrets: list[str] | tuple[str, ...], replacement: str = "[REDACTED]") -> str:
    """Remove configured secret values completely from text."""
    text = value or ""
    for secret in secrets:
        if secret and len(secret) > 4:
            text = text.replace(secret, replacement)
    return text
