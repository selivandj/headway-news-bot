from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTO_START = "<!-- AUTO_HISTORY_START -->"
AUTO_END = "<!-- AUTO_HISTORY_END -->"

APPROVED_ACTIONS = {"approved"}
REJECTED_ACTIONS = {"rejected", "cancelled"}
REWRITE_ACTIONS = {"rewrite_requested"}
MEDIA_ERROR_ACTIONS = {
    "bad_media",
    "media_not_found",
    "media_not_found_by_button",
    "media_removed",
}
MEDIA_WORK_ACTIONS = {
    "media_found_by_button",
    "media_replaced_by_precise_search",
    "generate_media_requested",
}

TOPIC_LABELS = {
    "charging_infrastructure": "зарядная инфраструктура",
    "fast_charging": "быстрые зарядки",
    "china_ev": "Китай",
    "subsidy_regulation": "субсидии и регулирование",
    "operators": "операторы",
    "charging_hub": "зарядные хабы",
    "battery_swap": "батарейный обмен",
    "standards": "стандарты GB/T / ChaoJi",
    "battery": "батареи",
    "russia": "Россия",
    "fleet_bus": "электробусы и флоты",
}


def utc_now_ts() -> int:
    return int(time.time())


def parse_state_file(path: str | Path) -> dict[str, str]:
    state_path = Path(path)
    if not state_path.exists():
        return {}
    data: dict[str, str] = {}
    for line in state_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def training_timer_enabled(timer_name: str = "headway-news-monitor-training7d.timer") -> bool | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-enabled", timer_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "enabled"
    except Exception:
        return None


def training_timer_active(timer_name: str = "headway-news-monitor-training7d.timer") -> bool | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", timer_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return None


def _parse_time_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        pass
    text = str(value)
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _connect(db_path: str | Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {str(row["name"]) for row in rows}


def _rows_since(conn, table: str, timestamp_column: str, since_ts: int) -> list[sqlite3.Row]:
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.Error:
        return []
    result = []
    for row in rows:
        row_ts = _parse_time_value(row[timestamp_column])
        if row_ts is not None and row_ts >= since_ts:
            result.append(row)
    return result


def _draft_rows_since(conn, since_ts: int) -> list[sqlite3.Row]:
    return _rows_since(conn, "drafts", "created_at", since_ts)


def _feedback_rows_since(conn, since_ts: int) -> list[sqlite3.Row]:
    return _rows_since(conn, "owner_feedback", "timestamp", since_ts)


def _article_rows_since(conn, since_ts: int) -> list[sqlite3.Row]:
    if "articles" not in _table_names(conn):
        return []
    return _rows_since(conn, "articles", "created_at", since_ts)


def _topic_tags(row: sqlite3.Row) -> list[str]:
    try:
        data = json.loads(row["topic_tags"] or "[]")
        return [str(item) for item in data if str(item).strip()]
    except Exception:
        return []


def _safe_percent(part: int, total: int) -> float:
    return round(100.0 * part / max(total, 1), 1)


def _source_recommendation(total: int, approval_rate: float, media_success_rate: float, rejected: int) -> str:
    if total >= 3 and (approval_rate < 30 or rejected >= max(3, total // 2 + 1)):
        return "disable"
    if approval_rate >= 60 and media_success_rate >= 50:
        return "keep"
    return "watch"


def source_quality_rows(db_path: str | Path, days: int = 7) -> list[dict[str, Any]]:
    cutoff = utc_now_ts() - int(days) * 86400
    if not Path(db_path).exists():
        return []
    with _connect(db_path) as conn:
        if "drafts" not in _table_names(conn):
            return []
        rows = _draft_rows_since(conn, cutoff)

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = str(row["source"] or "unknown")
        item = grouped.setdefault(
            source,
            {
                "source_name": source,
                "total_drafts": 0,
                "approved": 0,
                "rejected": 0,
                "with_media": 0,
                "bad_media": 0,
                "last_success_at": "",
            },
        )
        item["total_drafts"] += 1
        status = str(row["status"] or "")
        if status == "approved":
            item["approved"] += 1
            last_success = row["processed_at"] or row["created_at"] or ""
            if str(last_success) > str(item["last_success_at"]):
                item["last_success_at"] = str(last_success)
        if status in {"rejected", "cancelled"}:
            item["rejected"] += 1
        has_media = int(row["has_media"] or 0) == 1
        bad_media = int(row["bad_media_flag"] or 0) == 1
        if has_media:
            item["with_media"] += 1
        if bad_media:
            item["bad_media"] += 1

    result = []
    for item in grouped.values():
        total = int(item["total_drafts"])
        media_ok = int(item["with_media"]) - int(item["bad_media"])
        approval_rate = _safe_percent(int(item["approved"]), total)
        media_success_rate = _safe_percent(max(media_ok, 0), total)
        item["approval_rate"] = approval_rate
        item["media_success_rate"] = media_success_rate
        item["recommendation"] = _source_recommendation(
            total,
            approval_rate,
            media_success_rate,
            int(item["rejected"]),
        )
        result.append(item)
    return sorted(result, key=lambda row: (row["approval_rate"], row["total_drafts"]), reverse=True)


def bad_source_rows(db_path: str | Path, days: int = 7) -> list[dict[str, Any]]:
    cutoff = utc_now_ts() - int(days) * 86400
    quality = source_quality_rows(db_path, days=days)
    if not Path(db_path).exists():
        return []
    reason_by_source: dict[str, Counter] = defaultdict(Counter)
    with _connect(db_path) as conn:
        tables = _table_names(conn)
        if {"drafts", "owner_feedback"}.issubset(tables):
            rows = conn.execute(
                """
                SELECT d.source, f.action, f.feedback_text, f.timestamp
                FROM owner_feedback f
                LEFT JOIN drafts d ON d.id = f.draft_id
                """
            ).fetchall()
            for row in rows:
                ts = _parse_time_value(row["timestamp"])
                if ts is None or ts < cutoff:
                    continue
                source = str(row["source"] or "unknown")
                action = str(row["action"] or "")
                text = str(row["feedback_text"] or "").lower()
                if action in MEDIA_ERROR_ACTIONS or "медиа" in text or "фото" in text:
                    reason_by_source[source]["media"] += 1
                if "нет цифр" in text or "мало фак" in text or "фактур" in text:
                    reason_by_source[source]["facts"] += 1
                if "не про заряд" in text or "инфраструктур" in text:
                    reason_by_source[source]["not_charging"] += 1

    rows = []
    for item in quality:
        reasons = reason_by_source.get(item["source_name"], Counter())
        bad_signal = (
            item["total_drafts"] >= 3 and item["approval_rate"] < 30
        ) or item["rejected"] >= 3 or reasons["media"] >= 2 or reasons["facts"] >= 2 or reasons["not_charging"] >= 1
        if not bad_signal:
            continue
        copy = dict(item)
        copy["media_problems"] = reasons["media"] + int(item["bad_media"])
        copy["low_fact_reasons"] = reasons["facts"]
        copy["not_charging_reasons"] = reasons["not_charging"]
        copy["note"] = "Источник можно отключить, если тенденция сохранится."
        rows.append(copy)
    return sorted(rows, key=lambda row: (row["approval_rate"], -row["rejected"]))


def good_topic_rows(db_path: str | Path, days: int = 7) -> list[dict[str, Any]]:
    cutoff = utc_now_ts() - int(days) * 86400
    if not Path(db_path).exists():
        return []
    counters: dict[str, Counter] = defaultdict(Counter)
    with _connect(db_path) as conn:
        if "drafts" not in _table_names(conn):
            return []
        for row in _draft_rows_since(conn, cutoff):
            for tag in _topic_tags(row):
                counters[tag]["total"] += 1
                if str(row["status"] or "") == "approved":
                    counters[tag]["approved"] += 1
                if str(row["status"] or "") in {"rejected", "cancelled"}:
                    counters[tag]["rejected"] += 1

    rows = []
    for tag, counter in counters.items():
        total = counter["total"]
        approved = counter["approved"]
        rows.append(
            {
                "topic_tag": tag,
                "topic_name": TOPIC_LABELS.get(tag, tag),
                "total": total,
                "approved": approved,
                "rejected": counter["rejected"],
                "approval_rate": _safe_percent(approved, total),
            }
        )
    return sorted(rows, key=lambda row: (row["approval_rate"], row["approved"], row["total"]), reverse=True)


def rejection_reason_counts(db_path: str | Path, days: int = 7, limit: int = 8) -> list[tuple[str, int]]:
    cutoff = utc_now_ts() - int(days) * 86400
    if not Path(db_path).exists():
        return []
    counter: Counter[str] = Counter()
    with _connect(db_path) as conn:
        if "owner_feedback" not in _table_names(conn):
            return []
        for row in _feedback_rows_since(conn, cutoff):
            action = str(row["action"] or "")
            text = " ".join(str(row["feedback_text"] or "").split())
            if action in {"rejected", "bad_media"} and text:
                counter[text[:100]] += 1
    return counter.most_common(limit)


def owner_decision_count(db_path: str | Path, days: int = 7) -> int:
    cutoff = utc_now_ts() - int(days) * 86400
    if not Path(db_path).exists():
        return 0
    with _connect(db_path) as conn:
        if "owner_feedback" not in _table_names(conn):
            return 0
        return len(
            [
                row
                for row in _feedback_rows_since(conn, cutoff)
                if str(row["action"] or "") != "created"
            ]
        )


def training_status(db_path: str | Path, state_path: str | Path, now_ts: int | None = None) -> dict[str, Any]:
    now_ts = now_ts or utc_now_ts()
    state = parse_state_file(state_path)
    started_at = int(state.get("STARTED_AT") or 0)
    deadline_at = int(state.get("DEADLINE_AT") or 0)
    since_ts = started_at or (now_ts - 7 * 86400)
    timer_active = training_timer_active()
    timer_enabled = training_timer_enabled()
    enabled = bool(state) and (deadline_at == 0 or now_ts < deadline_at)
    if timer_active is not None:
        enabled = enabled and timer_active

    result = {
        "enabled": enabled,
        "timer_active": timer_active,
        "timer_enabled": timer_enabled,
        "started_at": started_at,
        "deadline_at": deadline_at,
        "hours_left": max(0.0, round((deadline_at - now_ts) / 3600, 1)) if deadline_at else 0.0,
        "runs": int(state.get("RUNS") or 0),
        "target_drafts_per_day": int(state.get("TARGET_DRAFTS_PER_DAY") or 2),
        "candidates_collected": 0,
        "drafts_created": 0,
        "published": 0,
        "rejected": 0,
        "rewrite_requested": 0,
        "media_errors": 0,
    }
    if not Path(db_path).exists():
        return result
    with _connect(db_path) as conn:
        tables = _table_names(conn)
        if "articles" in tables:
            result["candidates_collected"] = len(_article_rows_since(conn, since_ts))
        if "drafts" in tables:
            drafts = _draft_rows_since(conn, since_ts)
            result["drafts_created"] = len(drafts)
            result["published"] = sum(1 for row in drafts if str(row["status"] or "") == "approved")
            result["rejected"] = sum(1 for row in drafts if str(row["status"] or "") in {"rejected", "cancelled"})
            result["media_errors"] += sum(1 for row in drafts if int(row["bad_media_flag"] or 0) == 1)
        if "owner_feedback" in tables:
            feedback = _feedback_rows_since(conn, since_ts)
            result["rewrite_requested"] = sum(1 for row in feedback if str(row["action"] or "") in REWRITE_ACTIONS)
            result["media_errors"] += sum(1 for row in feedback if str(row["action"] or "") in MEDIA_ERROR_ACTIONS)
    return result


def format_training_status(status: dict[str, Any]) -> str:
    if not status.get("started_at"):
        return "Training mode: no state file yet. Таймер еще не начал накопление статистики."
    started = datetime.fromtimestamp(int(status["started_at"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join(
        [
            "7-day editorial training",
            f"Enabled: {'yes' if status.get('enabled') else 'no'}",
            f"Timer active: {status.get('timer_active')}",
            f"Timer enabled: {status.get('timer_enabled')}",
            f"Started: {started}",
            f"Hours left: {status.get('hours_left')}",
            f"Runs: {status.get('runs')}",
            f"Daily target: {status.get('target_drafts_per_day')} strong drafts",
            "",
            f"Candidates collected: {status.get('candidates_collected')}",
            f"Drafts created: {status.get('drafts_created')}",
            f"Published by owner: {status.get('published')}",
            f"Rejected/cancelled: {status.get('rejected')}",
            f"Rewrite requested: {status.get('rewrite_requested')}",
            f"Media errors: {status.get('media_errors')}",
        ]
    )


def format_source_quality(rows: list[dict[str, Any]], days: int) -> str:
    if not rows:
        return f"Source quality за {days} дней: данных пока мало."
    lines = [f"Source quality за {days} дней:"]
    for row in rows[:12]:
        lines.append(
            "- {source}: drafts={total}, approved={approved}, rejected={rejected}, "
            "approval={approval}%, media={media}%, last_success={last}, rec={rec}".format(
                source=row["source_name"],
                total=row["total_drafts"],
                approved=row["approved"],
                rejected=row["rejected"],
                approval=row["approval_rate"],
                media=row["media_success_rate"],
                last=row["last_success_at"] or "-",
                rec=row["recommendation"],
            )
        )
    return "\n".join(lines)


def format_bad_sources(rows: list[dict[str, Any]], days: int) -> str:
    if not rows:
        return f"Bad sources за {days} дней: явного мусора пока не видно."
    lines = [f"Bad sources за {days} дней:"]
    for row in rows[:10]:
        lines.append(
            "- {source}: approval={approval}%, rejected={rejected}, media_problems={media}, "
            "low_fact={facts}, not_charging={not_charging}. {note}".format(
                source=row["source_name"],
                approval=row["approval_rate"],
                rejected=row["rejected"],
                media=row["media_problems"],
                facts=row["low_fact_reasons"],
                not_charging=row["not_charging_reasons"],
                note=row["note"],
            )
        )
    return "\n".join(lines)


def format_good_topics(rows: list[dict[str, Any]], days: int) -> str:
    if not rows:
        return f"Good topics за {days} дней: данных пока мало."
    lines = [f"Good topics за {days} дней:"]
    for row in rows[:12]:
        lines.append(
            f"- {row['topic_name']}: approved={row['approved']}/{row['total']}, approval={row['approval_rate']}%"
        )
    return "\n".join(lines)


def build_editorial_memory_auto_block(db_path: str | Path, days: int = 90, updated_at: datetime | None = None) -> str:
    updated_at = updated_at or datetime.now(timezone.utc)
    sources = source_quality_rows(db_path, days=days)
    bad_sources = bad_source_rows(db_path, days=days)
    topics = good_topic_rows(db_path, days=days)
    reasons = rejection_reason_counts(db_path, days=days)
    decisions = owner_decision_count(db_path, days=days)

    lines = [
        AUTO_START,
        "# Auto Editorial Memory",
        "",
        f"Updated: {updated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Period: {days} days",
        f"Owner decisions recorded: {decisions}",
        "",
        "## Best Sources",
    ]
    good_sources = [row for row in sources if row["recommendation"] == "keep"]
    lines.extend(
        [
            f"- {row['source_name']}: approval {row['approval_rate']}%, media {row['media_success_rate']}%"
            for row in good_sources[:8]
        ]
        or ["- Not enough data yet"]
    )
    lines.extend(["", "## Bad Sources"])
    lines.extend(
        [
            f"- {row['source_name']}: approval {row['approval_rate']}%, rejected {row['rejected']}. Watch, do not auto-disable."
            for row in bad_sources[:8]
        ]
        or ["- Not enough data yet"]
    )
    lines.extend(["", "## Good Topics"])
    lines.extend(
        [
            f"- {row['topic_name']}: {row['approved']}/{row['total']} approved, {row['approval_rate']}%"
            for row in topics[:10]
            if row["approved"] > 0
        ]
        or ["- Not enough data yet"]
    )
    lines.extend(["", "## Frequent Rejection Reasons"])
    lines.extend([f"- {reason} ({count})" for reason, count in reasons] or ["- Not enough data yet"])
    lines.extend(
        [
            "",
            "## Media Rules Learned",
            "- Prefer original article media.",
            "- If media is missing, use exact station/model/company search before generic search.",
            "- Mark bad media when the image is unrelated, duplicated, unsafe, or not showing the discussed charger/equipment.",
            "",
            "## Phrases To Avoid",
            "- Do not reuse phrases repeatedly rejected by the owner.",
            "- Keep recent rejection feedback in the prompt memory.",
            AUTO_END,
        ]
    )
    return "\n".join(lines) + "\n"


def update_editorial_memory_file(memory_path: str | Path, auto_block: str) -> dict[str, Any]:
    path = Path(memory_path)
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Headway Telegram Editorial Memory\n"
    if AUTO_START in existing and AUTO_END in existing:
        before = existing.split(AUTO_START, 1)[0].rstrip()
        after = existing.split(AUTO_END, 1)[1].lstrip()
        content = before + "\n\n" + auto_block + ("\n" + after if after else "")
    else:
        content = auto_block + "\n" + existing
    changed = content.rstrip() + "\n" != existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return {
        "changed": changed,
        "path": str(path),
        "sections": ["best_sources", "bad_sources", "good_topics", "rejection_reasons", "media_rules"],
    }


def update_training_editorial_memory(db_path: str | Path, memory_path: str | Path, days: int = 90) -> dict[str, Any]:
    block = build_editorial_memory_auto_block(db_path, days=days)
    return update_editorial_memory_file(memory_path, block)


def format_daily_training_report(db_path: str | Path, state_path: str | Path, days: int = 7) -> str:
    status = training_status(db_path, state_path)
    if not status.get("started_at"):
        return ""
    topics = good_topic_rows(db_path, days=days)[:5]
    bad_sources = bad_source_rows(db_path, days=days)[:5]
    reasons = rejection_reason_counts(db_path, days=days, limit=5)
    lines = [
        "",
        "Обучение канала за 7 дней",
        f"- Решений владельца накоплено: {owner_decision_count(db_path, days=days)}",
        f"- Черновиков создано: {status.get('drafts_created')}",
        f"- Опубликовано: {status.get('published')}",
        f"- Отклонено/снято: {status.get('rejected')}",
        f"- Переделок текста: {status.get('rewrite_requested')}",
        f"- Медиа-ошибок: {status.get('media_errors')}",
    ]
    if topics:
        lines.append("- Темы, которые чаще одобряются: " + ", ".join(row["topic_name"] for row in topics if row["approved"] > 0)[:180])
    if bad_sources:
        lines.append("- Худшие источники: " + ", ".join(row["source_name"] for row in bad_sources)[:180])
    if reasons:
        lines.append("- Частые причины отклонения: " + "; ".join(f"{reason} ({count})" for reason, count in reasons)[:250])
    lines.append("- editorial_memory.md обновляется в AUTO_HISTORY-блоке без перезаписи ручной части.")
    return "\n".join(lines)
