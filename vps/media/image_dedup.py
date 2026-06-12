from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image
    import imagehash
except Exception:  # pragma: no cover - optional deployment dependency
    Image = None
    imagehash = None


@dataclass(frozen=True)
class DuplicateMatch:
    file_path: str
    phash: str
    distance: int
    source_url: str = ""
    draft_id: str = ""


class ImageDeduplicator:
    """Perceptual image hash store for avoiding repeated media."""

    def __init__(
        self,
        db_path: str | Path = "vps/database/image_hashes.db",
        threshold: int = 10,
        enabled: bool = True,
    ):
        self.db_path = Path(db_path)
        self.threshold = int(threshold)
        self.enabled = enabled
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def available(self) -> bool:
        return self.enabled and Image is not None and imagehash is not None

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS image_hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    phash TEXT NOT NULL,
                    source_url TEXT,
                    draft_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_phash ON image_hashes(phash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_image_hashes_draft ON image_hashes(draft_id)")

    def compute_phash(self, file_path: str | Path) -> str:
        if not self.available:
            raise RuntimeError("ImageHash/Pillow is not available")
        with Image.open(file_path) as image:
            return str(imagehash.phash(image))

    @staticmethod
    def hash_distance(left: str, right: str) -> int:
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count()
        except Exception:
            return 9999

    def find_duplicate(self, file_path: str | Path, phash: str | None = None) -> DuplicateMatch | None:
        if not self.available:
            return None
        phash = phash or self.compute_phash(file_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT file_path, phash, source_url, draft_id FROM image_hashes ORDER BY id DESC"
            ).fetchall()
        best: DuplicateMatch | None = None
        for row in rows:
            distance = self.hash_distance(phash, row["phash"])
            if distance <= self.threshold and (best is None or distance < best.distance):
                best = DuplicateMatch(
                    file_path=row["file_path"],
                    phash=row["phash"],
                    distance=distance,
                    source_url=row["source_url"] or "",
                    draft_id=row["draft_id"] or "",
                )
        return best

    def register_image(
        self,
        file_path: str | Path,
        source_url: str = "",
        draft_id: str = "",
        phash: str | None = None,
    ) -> str:
        if not self.available:
            return ""
        phash = phash or self.compute_phash(file_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO image_hashes(file_path, phash, source_url, draft_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(file_path), phash, source_url, draft_id, datetime.now(timezone.utc).isoformat()),
            )
        return phash

    def check_and_register(
        self,
        file_path: str | Path,
        source_url: str = "",
        draft_id: str = "",
    ) -> dict:
        if not self.available:
            return {"duplicate": False, "registered": False, "reason": "disabled_or_unavailable"}

        phash = self.compute_phash(file_path)
        duplicate = self.find_duplicate(file_path, phash=phash)
        if duplicate:
            logging.info(
                "Image duplicate detected: %s is close to %s (distance=%s)",
                file_path,
                duplicate.file_path,
                duplicate.distance,
            )
            return {
                "duplicate": True,
                "registered": False,
                "phash": phash,
                "match": duplicate.__dict__,
            }

        self.register_image(file_path, source_url=source_url, draft_id=draft_id, phash=phash)
        return {"duplicate": False, "registered": True, "phash": phash}
