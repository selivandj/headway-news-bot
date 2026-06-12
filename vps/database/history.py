from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class HistoryDB:
    """SQLite history for draft quality and owner decisions."""

    def __init__(self, db_path: str | Path = "vps/database/history.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT '',
                    source_region TEXT,
                    llm_provider TEXT,
                    llm_model TEXT,
                    headline TEXT,
                    link TEXT,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    status TEXT NOT NULL,
                    has_media INTEGER DEFAULT 0,
                    media_status TEXT,
                    bad_media_flag INTEGER DEFAULT 0,
                    owner_feedback TEXT,
                    topic_tags TEXT,
                    freshness_hours REAL,
                    processed_at TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT UNIQUE NOT NULL,
                    total_drafts INTEGER DEFAULT 0,
                    approved_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    cancelled_count INTEGER DEFAULT 0,
                    last_used TEXT,
                    avg_freshness_hours REAL,
                    quality_score REAL DEFAULT 0.0
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS providers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_name TEXT UNIQUE NOT NULL,
                    total_attempts INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    reject_count INTEGER DEFAULT 0,
                    last_used TEXT,
                    success_rate REAL DEFAULT 0.0,
                    avg_response_time_ms REAL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    draft_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    feedback_text TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (draft_id) REFERENCES drafts(id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic_tag TEXT UNIQUE NOT NULL,
                    total_count INTEGER DEFAULT 0,
                    approved_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    approval_rate REAL DEFAULT 0.0
                )
                """
            )

    def draft_id(self, draft_data: dict[str, Any], fallback: str = "") -> str:
        value = (
            draft_data.get("id")
            or draft_data.get("draft_id")
            or draft_data.get("article_id")
            or draft_data.get("link")
            or draft_data.get("title")
            or draft_data.get("headline")
            or fallback
        )
        value = str(value or "").strip()
        if not value:
            value = datetime.now(timezone.utc).isoformat()
        if len(value) <= 80 and all(ch.isalnum() or ch in "-_" for ch in value):
            return value
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def record_draft_created(self, draft_data: dict[str, Any], fallback_id: str = "") -> str:
        draft_id = self.draft_id(draft_data, fallback=fallback_id)
        topic_tags = self._extract_topic_tags(draft_data)
        source = str(draft_data.get("source") or "unknown")
        created_at = self._normalize_time(draft_data.get("created_at"))
        freshness_hours = self._safe_float(draft_data.get("freshness_hours"))
        has_media = 1 if self._draft_has_media(draft_data) else 0
        headline = str(draft_data.get("headline") or draft_data.get("title") or "")[:240]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            existing = cursor.execute("SELECT id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            cursor.execute(
                """
                INSERT INTO drafts (
                    id, source, source_region, llm_provider, llm_model, headline, link,
                    created_at, published_at, status, has_media, media_status, topic_tags,
                    freshness_hours
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    source_region = excluded.source_region,
                    llm_provider = COALESCE(excluded.llm_provider, drafts.llm_provider),
                    llm_model = COALESCE(excluded.llm_model, drafts.llm_model),
                    headline = COALESCE(NULLIF(excluded.headline, ''), drafts.headline),
                    link = COALESCE(NULLIF(excluded.link, ''), drafts.link),
                    has_media = excluded.has_media,
                    media_status = COALESCE(excluded.media_status, drafts.media_status),
                    topic_tags = excluded.topic_tags,
                    freshness_hours = COALESCE(excluded.freshness_hours, drafts.freshness_hours)
                """,
                (
                    draft_id,
                    source,
                    draft_data.get("region") or draft_data.get("source_region") or "unknown",
                    draft_data.get("llm_provider"),
                    draft_data.get("llm_model"),
                    headline,
                    draft_data.get("link") or "",
                    created_at,
                    self._normalize_time(draft_data.get("published_at"), allow_empty=True),
                    draft_data.get("status") or "pending",
                    has_media,
                    draft_data.get("media_status") or "not_checked",
                    json.dumps(topic_tags, ensure_ascii=False),
                    freshness_hours,
                ),
            )

            if existing is None:
                self._update_source_stats(conn, source, "created", freshness_hours=freshness_hours)
                provider = draft_data.get("llm_provider")
                if provider:
                    self._update_provider_stats(conn, str(provider), "attempt")
                for tag in topic_tags:
                    self._update_topic_stats(conn, tag, "created")
                self._record_feedback(conn, draft_id, "created", None)
        return draft_id

    def record_draft_approved(self, draft_id: str, feedback: str | None = None, draft_data: dict[str, Any] | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, draft_data)
            conn.execute(
                """
                UPDATE drafts
                SET status = 'approved', processed_at = ?, owner_feedback = ?
                WHERE id = ?
                """,
                (self._now(), feedback, draft_id),
            )
            row = conn.execute("SELECT source, llm_provider, topic_tags FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row:
                self._update_source_stats(conn, row["source"], "approved")
                if row["llm_provider"]:
                    self._update_provider_stats(conn, row["llm_provider"], "success")
                self._update_topics_from_row(conn, row, "approved")
            self._record_feedback(conn, draft_id, "approved", feedback)

    def record_draft_rejected(self, draft_id: str, reason: str | None = None, draft_data: dict[str, Any] | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, draft_data)
            conn.execute(
                """
                UPDATE drafts
                SET status = 'rejected', processed_at = ?, owner_feedback = ?
                WHERE id = ?
                """,
                (self._now(), reason, draft_id),
            )
            row = conn.execute("SELECT source, llm_provider, topic_tags FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row:
                self._update_source_stats(conn, row["source"], "rejected")
                if row["llm_provider"]:
                    self._update_provider_stats(conn, row["llm_provider"], "reject")
                self._update_topics_from_row(conn, row, "rejected")
            self._record_feedback(conn, draft_id, "rejected", reason)

    def record_draft_cancelled(self, draft_id: str, reason: str | None = None, draft_data: dict[str, Any] | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, draft_data)
            conn.execute(
                """
                UPDATE drafts
                SET status = 'cancelled', processed_at = ?, owner_feedback = ?
                WHERE id = ?
                """,
                (self._now(), reason, draft_id),
            )
            row = conn.execute("SELECT source FROM drafts WHERE id = ?", (draft_id,)).fetchone()
            if row:
                self._update_source_stats(conn, row["source"], "cancelled")
            self._record_feedback(conn, draft_id, "cancelled", reason)

    def record_bad_media(self, draft_id: str, feedback: str | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, None)
            conn.execute(
                """
                UPDATE drafts
                SET bad_media_flag = 1,
                    owner_feedback = TRIM(COALESCE(owner_feedback, '') || ' [BAD_MEDIA] ' || COALESCE(?, ''))
                WHERE id = ?
                """,
                (feedback, draft_id),
            )
            self._record_feedback(conn, draft_id, "bad_media", feedback)

    def record_feedback(self, draft_id: str, action: str, feedback_text: str | None = None, draft_data: dict[str, Any] | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, draft_data)
            self._record_feedback(conn, draft_id, action, feedback_text)

    def update_media_status(self, draft_id: str, media_status: str, has_media: bool | None = None, draft_data: dict[str, Any] | None = None) -> None:
        with self._get_connection() as conn:
            self._ensure_draft(conn, draft_id, draft_data)
            if has_media is None:
                conn.execute("UPDATE drafts SET media_status = ? WHERE id = ?", (media_status, draft_id))
            else:
                conn.execute(
                    "UPDATE drafts SET media_status = ?, has_media = ? WHERE id = ?",
                    (media_status, 1 if has_media else 0, draft_id),
                )

    def get_statistics(self, days: int = 30) -> dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            general = cursor.execute(
                """
                SELECT
                    COUNT(*) as total_drafts,
                    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) as cancelled,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN has_media = 1 THEN 1 ELSE 0 END) as with_media,
                    SUM(CASE WHEN bad_media_flag = 1 THEN 1 ELSE 0 END) as bad_media
                FROM drafts
                WHERE created_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            top_sources = cursor.execute(
                """
                SELECT source_name, total_drafts, approved_count, rejected_count, cancelled_count,
                       ROUND(100.0 * approved_count / NULLIF(total_drafts, 0), 1) as approval_rate,
                       quality_score
                FROM sources
                WHERE total_drafts >= 1
                ORDER BY approval_rate DESC, total_drafts DESC
                LIMIT 10
                """
            ).fetchall()
            problem_sources = cursor.execute(
                """
                SELECT source_name, total_drafts, approved_count, rejected_count,
                       ROUND(100.0 * approved_count / NULLIF(total_drafts, 0), 1) as approval_rate
                FROM sources
                WHERE total_drafts >= 3
                ORDER BY approval_rate ASC, total_drafts DESC
                LIMIT 10
                """
            ).fetchall()
            providers = cursor.execute(
                """
                SELECT provider_name, total_attempts, success_count, reject_count,
                       ROUND(100.0 * success_count / NULLIF(total_attempts, 0), 1) as success_rate
                FROM providers
                ORDER BY success_rate DESC, total_attempts DESC
                """
            ).fetchall()
            topics = cursor.execute(
                """
                SELECT topic_tag, total_count, approved_count, rejected_count,
                       ROUND(100.0 * approved_count / NULLIF(total_count, 0), 1) as approval_rate
                FROM topics
                WHERE total_count >= 1
                ORDER BY total_count DESC, approval_rate DESC
                LIMIT 15
                """
            ).fetchall()

            total = general["total_drafts"] or 0
            approved = general["approved"] or 0
            return {
                "period_days": days,
                "general": {
                    "total_drafts": total,
                    "approved": approved,
                    "rejected": general["rejected"] or 0,
                    "cancelled": general["cancelled"] or 0,
                    "pending": general["pending"] or 0,
                    "with_media": general["with_media"] or 0,
                    "bad_media": general["bad_media"] or 0,
                    "approval_rate": round(100.0 * approved / max(total, 1), 1),
                },
                "top_sources": [dict(row) for row in top_sources],
                "problem_sources": [dict(row) for row in problem_sources],
                "providers": [dict(row) for row in providers],
                "topics": [dict(row) for row in topics],
            }

    def get_source_quality(self, source: str) -> dict[str, Any]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM sources WHERE source_name = ?", (source,)).fetchone()
            return dict(row) if row else {}

    def get_recent_feedback(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT draft_id, action, feedback_text, timestamp
                FROM owner_feedback
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_rejection_feedback(self, limit: int = 5) -> list[str]:
        """Return recent owner rejection reasons for prompt-time learning."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT feedback_text
                FROM owner_feedback
                WHERE action = 'rejected'
                  AND feedback_text IS NOT NULL
                  AND TRIM(feedback_text) != ''
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            feedbacks: list[str] = []
            seen: set[str] = set()
            for row in rows:
                text = " ".join(str(row["feedback_text"] or "").split())
                if not text:
                    continue
                text = text[:150] + ("..." if len(text) > 150 else "")
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                feedbacks.append(text)
            return feedbacks

    def _ensure_draft(self, conn, draft_id: str, draft_data: dict[str, Any] | None) -> None:
        if conn.execute("SELECT id FROM drafts WHERE id = ?", (draft_id,)).fetchone():
            return
        data = dict(draft_data or {})
        data["id"] = draft_id
        topic_tags = self._extract_topic_tags(data)
        conn.execute(
            """
            INSERT OR IGNORE INTO drafts (
                id, source, source_region, llm_provider, llm_model, headline, link,
                created_at, status, has_media, media_status, topic_tags
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                data.get("source") or "unknown",
                data.get("region") or "unknown",
                data.get("llm_provider"),
                data.get("llm_model"),
                data.get("headline") or data.get("title") or draft_id,
                data.get("link") or "",
                self._normalize_time(data.get("created_at")),
                data.get("status") or "pending",
                1 if self._draft_has_media(data) else 0,
                data.get("media_status") or "not_checked",
                json.dumps(topic_tags, ensure_ascii=False),
            ),
        )

    def _update_source_stats(self, conn, source: str, action: str, freshness_hours: float | None = None) -> None:
        if not source:
            return
        now = self._now()
        conn.execute(
            """
            INSERT OR IGNORE INTO sources (source_name, last_used)
            VALUES (?, ?)
            """,
            (source, now),
        )
        if action == "created":
            conn.execute(
                """
                UPDATE sources
                SET total_drafts = total_drafts + 1,
                    last_used = ?,
                    avg_freshness_hours = CASE
                        WHEN ? IS NULL THEN avg_freshness_hours
                        WHEN avg_freshness_hours IS NULL THEN ?
                        ELSE ROUND((avg_freshness_hours * (total_drafts - 1) + ?) / total_drafts, 2)
                    END
                WHERE source_name = ?
                """,
                (now, freshness_hours, freshness_hours, freshness_hours, source),
            )
        elif action == "approved":
            conn.execute("UPDATE sources SET approved_count = approved_count + 1, last_used = ? WHERE source_name = ?", (now, source))
        elif action == "rejected":
            conn.execute("UPDATE sources SET rejected_count = rejected_count + 1, last_used = ? WHERE source_name = ?", (now, source))
        elif action == "cancelled":
            conn.execute("UPDATE sources SET cancelled_count = cancelled_count + 1, last_used = ? WHERE source_name = ?", (now, source))
        conn.execute(
            """
            UPDATE sources
            SET quality_score = ROUND(100.0 * approved_count / NULLIF(total_drafts, 0), 1)
            WHERE source_name = ?
            """,
            (source,),
        )

    def _update_provider_stats(self, conn, provider: str, action: str) -> None:
        if not provider:
            return
        now = self._now()
        conn.execute(
            "INSERT OR IGNORE INTO providers (provider_name, last_used) VALUES (?, ?)",
            (provider, now),
        )
        if action == "attempt":
            conn.execute("UPDATE providers SET total_attempts = total_attempts + 1, last_used = ? WHERE provider_name = ?", (now, provider))
        elif action == "success":
            conn.execute("UPDATE providers SET success_count = success_count + 1, last_used = ? WHERE provider_name = ?", (now, provider))
        elif action == "reject":
            conn.execute("UPDATE providers SET reject_count = reject_count + 1, last_used = ? WHERE provider_name = ?", (now, provider))
        conn.execute(
            """
            UPDATE providers
            SET success_rate = ROUND(100.0 * success_count / NULLIF(total_attempts, 0), 1)
            WHERE provider_name = ?
            """,
            (provider,),
        )

    def _update_topic_stats(self, conn, topic: str, action: str) -> None:
        if not topic:
            return
        conn.execute("INSERT OR IGNORE INTO topics (topic_tag) VALUES (?)", (topic,))
        if action == "created":
            conn.execute("UPDATE topics SET total_count = total_count + 1 WHERE topic_tag = ?", (topic,))
        elif action == "approved":
            conn.execute("UPDATE topics SET approved_count = approved_count + 1 WHERE topic_tag = ?", (topic,))
        elif action == "rejected":
            conn.execute("UPDATE topics SET rejected_count = rejected_count + 1 WHERE topic_tag = ?", (topic,))
        conn.execute(
            """
            UPDATE topics
            SET approval_rate = ROUND(100.0 * approved_count / NULLIF(total_count, 0), 1)
            WHERE topic_tag = ?
            """,
            (topic,),
        )

    def _update_topics_from_row(self, conn, row, action: str) -> None:
        try:
            tags = json.loads(row["topic_tags"] or "[]")
        except Exception:
            tags = []
        for tag in tags:
            self._update_topic_stats(conn, tag, action)

    def _record_feedback(self, conn, draft_id: str, action: str, feedback_text: str | None = None) -> None:
        conn.execute(
            """
            INSERT INTO owner_feedback (draft_id, action, feedback_text, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (draft_id, action, feedback_text, self._now()),
        )

    def _extract_topic_tags(self, draft_data: dict[str, Any]) -> list[str]:
        text = " ".join(
            str(draft_data.get(key) or "")
            for key in ("headline", "title", "lead", "text", "summary", "source", "region")
        ).lower()
        tags: list[str] = []
        rules = [
            ("tesla", ("tesla", "supercharger")),
            ("china_ev", ("byd", "nio", "xpeng", "li auto", "китай", "china", "cnevpost", "carnewschina")),
            ("charging_infrastructure", ("заряд", "charging", "charger", "evse", "эзс", "станц")),
            ("battery", ("батаре", "battery", "аккумулятор", "cell")),
            ("battery_swap", ("swap", "замен", "battery swap")),
            ("russia", ("росси", "москва", "петербург", "рф", "russia")),
            ("fast_charging", ("fast", "быстр", "dc", "ультра", "мвт", "kw", "квт")),
            ("subsidy_regulation", ("субсид", "льгот", "минпромторг", "постанов", "ocpp", "ocpi")),
            ("fleet_bus", ("электробус", "автобус", "fleet", "депо")),
            ("charging_hub", ("hub", "хаб", "контейнер", "мультизаряд")),
        ]
        for tag, markers in rules:
            if any(marker in text for marker in markers):
                tags.append(tag)
        return tags or ["general"]

    @staticmethod
    def _draft_has_media(draft_data: dict[str, Any]) -> bool:
        return bool(draft_data.get("media") or draft_data.get("images"))

    @staticmethod
    def _safe_float(value) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_time(value, allow_empty: bool = False) -> str | None:
        if value in (None, ""):
            return None if allow_empty else datetime.now(timezone.utc).isoformat()
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        return str(value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
