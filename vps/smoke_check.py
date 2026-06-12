from __future__ import annotations

import py_compile
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def read_text(relative_path: str) -> str:
    return (BASE_DIR / relative_path).read_text(encoding="utf-8", errors="ignore")


def read_first_existing(*paths: str) -> str:
    for path_text in paths:
        path = Path(path_text)
        if not path.is_absolute():
            path = BASE_DIR / path
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def check(condition: bool, message: str, errors: list[str]) -> None:
    if condition:
        print(f"OK: {message}")
    else:
        print(f"FAIL: {message}")
        errors.append(message)


def compile_file(relative_path: str, errors: list[str]) -> None:
    path = BASE_DIR / relative_path
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"OK: compiles {relative_path}")
    except Exception as exc:
        print(f"FAIL: compiles {relative_path}: {exc}")
        errors.append(f"{relative_path}: {exc}")


def main() -> int:
    errors: list[str] = []

    for relative_path in (
        "bot.py",
        "monitor.py",
        "env_loader.py",
        "database/history.py",
        "keyboards.py",
        "backup.py",
        "media/media_status.py",
        "media/image_dedup.py",
        "media/precise_image_search.py",
        "parsers/china_sources.py",
        "parsers/sogou_image_search.py",
        "search/cache.py",
        "security/rate_limit.py",
        "security/redaction.py",
        "tests/test_static_guards.py",
    ):
        compile_file(relative_path, errors)

    bot_py = read_text("bot.py")
    monitor_py = read_text("monitor.py")
    service_py = read_first_existing("headway-news-monitor.service", "/etc/systemd/system/headway-news-monitor.service")
    china_service_py = read_first_existing("headway-china-daily-report.service", "/etc/systemd/system/headway-china-daily-report.service")
    training_timer = read_first_existing("headway-news-monitor-training7d.timer", "/etc/systemd/system/headway-news-monitor-training7d.timer")
    training_script = read_text("run_training_monitor.sh")
    history_py = read_text("database/history.py")
    backup_py = read_text("backup.py")
    env_loader_py = read_text("env_loader.py")
    redaction_py = read_text("security/redaction.py")
    rules_md = read_text("channel_agent_rules.md")

    check("load_headway_env(BASE_DIR)" in bot_py, "Bot loads root and vps .env through shared loader", errors)
    check("root / \".env\"" in env_loader_py and "root / \"vps\" / \".env\"" in env_loader_py, "Env loader supports root and vps .env", errors)
    check("app.add_error_handler(error_handler)" in bot_py, "Telegram bot has owner-visible error handler", errors)
    check("backup_now_command" in bot_py, "Telegram bot has owner-only backup command", errors)
    check("backup_all_sqlite_databases" in backup_py, "Backup module supports all SQLite databases", errors)
    check("RATE_LIMIT_OWNER_BYPASS" in bot_py, "Owner can bypass command rate limit", errors)
    check("[REDACTED]" in redaction_py, "Secret redaction removes token fragments", errors)
    check("confirm_publish_keyboard" in bot_py, "Inline publish path asks for confirmation", errors)
    check("notify_monitor_failure" in monitor_py, "Monitor reports fatal failures to review chat", errors)
    check("collect_china_candidates(24, force=True)" in monitor_py, "China daily report refreshes sources before reporting", errors)
    check("record_draft_history(draft, aid)" in monitor_py, "Normal drafts are recorded with article id, not undefined version id", errors)
    check("--send-review" in service_py, "Main monitor service sends review/failure messages", errors)
    check("--send-review" in china_service_py, "China daily report sends review/failure messages", errors)
    check("headway-news-monitor-training7d.service" in training_timer, "7-day training timer points at training service", errors)
    check("TRAINING_TARGET_DRAFTS_PER_DAY" in training_script, "7-day training monitor keeps daily target setting", errors)
    check("get_recent_rejection_feedback" in history_py, "History DB exposes recent rejection feedback for prompt memory", errors)
    check("Do not write for marketers" in rules_md, "Channel rules keep posts away from marketer framing", errors)
    check("If the article is not actually about Russia" in rules_md, "Channel rules forbid fake Russia labeling", errors)

    if errors:
        print("\nSmoke check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nSmoke check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
