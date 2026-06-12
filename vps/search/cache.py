from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class SearchCache:
    """Small JSON cache for free search/RSS helper calls."""

    def __init__(self, cache_dir: str | Path, ttl_hours: int = 24):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            created_at = datetime.fromisoformat(payload["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - created_at > self.ttl:
                return None
            return payload.get("data")
        except Exception:
            return None

    def set(self, key: str, data: Any) -> None:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        self._path(key).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

