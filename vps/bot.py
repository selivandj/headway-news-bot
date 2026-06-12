import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import textwrap
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from backup import backup_all_sqlite_databases
from env_loader import load_headway_env
from keyboards import (
    confirm_publish_keyboard,
    draft_action_keyboard as build_draft_action_keyboard,
    draft_callback_id,
    reject_reason_keyboard,
    reject_reason_label,
)
from security.rate_limit import RateLimiter, should_bypass_rate_limit
from security.redaction import redact_secrets as redact_secret_values

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:
    AsyncIOScheduler = None
    CronTrigger = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    Image = ImageDraw = ImageFont = None

try:
    from media.precise_image_search import PreciseImageSearch
except Exception:
    PreciseImageSearch = None

try:
    from media.media_status import MediaStatusManager
except Exception:
    MediaStatusManager = None

try:
    from database.history import HistoryDB
except Exception:
    HistoryDB = None

BASE_DIR = Path(__file__).resolve().parent
ENV_FILES_LOADED = load_headway_env(BASE_DIR)
DRAFT_DIR = BASE_DIR / "drafts"
PUBLISHED_DIR = BASE_DIR / "published"
CANCELLED_DIR = BASE_DIR / "cancelled"
FEEDBACK_DIR = BASE_DIR / "feedback"
DATABASE_DIR = BASE_DIR / "database"
DB_PATH = DATABASE_DIR / "history.db"
VOICE_DIR = BASE_DIR / "media" / "voice"
STORY_DIR = BASE_DIR / "media" / "storage" / "images"
REVIEW_MAP_PATH = BASE_DIR / "review_map.json"
EDITORIAL_MEMORY_PATH = BASE_DIR / "editorial_memory.md"
CHANNEL_AGENT_RULES_PATH = BASE_DIR / "channel_agent_rules.md"
GLOBAL_FEEDBACK_PATH = FEEDBACK_DIR / "owner_memory.log"

for folder in (DRAFT_DIR, PUBLISHED_DIR, CANCELLED_DIR, FEEDBACK_DIR, DATABASE_DIR, VOICE_DIR, STORY_DIR):
    folder.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REVIEW_CHAT_ID = int(os.environ["TELEGRAM_REVIEW_CHAT_ID"])
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ADMIN_TELEGRAM_ID = int(os.environ.get("ADMIN_TELEGRAM_ID") or REVIEW_CHAT_ID)
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID") or ADMIN_TELEGRAM_ID or REVIEW_CHAT_ID)
ERROR_NOTIFICATIONS_ENABLED = os.environ.get("ERROR_NOTIFICATIONS_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
RATE_LIMIT_REQUESTS = int(os.environ.get("RATE_LIMIT_REQUESTS") or "30")
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS") or "60")
RATE_LIMIT_OWNER_BYPASS = os.environ.get("RATE_LIMIT_OWNER_BYPASS", "1").lower() in {"1", "true", "yes", "on"}
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
BACKUP_KEEP_DAYS = int(os.environ.get("BACKUP_KEEP_DAYS") or "14")
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or (BASE_DIR / "backups"))
if not BACKUP_DIR.is_absolute():
    BACKUP_DIR = BASE_DIR / BACKUP_DIR
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or ""
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
OPENAI_TRANSCRIBE_MODEL = os.environ.get("OPENAI_TRANSCRIBE_MODEL") or "whisper-1"
KIMI_API_KEY = os.environ.get("KIMI_API_KEY") or ""
KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL") or "https://api.moonshot.ai/v1"
KIMI_MODEL = os.environ.get("KIMI_MODEL") or "moonshot-v1-8k"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY") or ""
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL") or "moonshotai/kimi-k2.6:free"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or ""
GEMINI_BASE_URL = os.environ.get("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash-lite"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or ""
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("GROQ_MODEL") or "llama-3.3-70b-versatile"
LLM_PROVIDER_CHAIN = os.environ.get("LLM_PROVIDER_CHAIN") or ""
LLM_PROVIDER = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
DAILY_REPORT_ENABLED = os.environ.get("DAILY_REPORT_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
DAILY_REPORT_TIME = os.environ.get("DAILY_REPORT_TIME") or "09:00"
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1").lower() in {"1", "true", "yes", "on"}
SCHEDULER_TIMEZONE = os.environ.get("SCHEDULER_TIMEZONE") or "Asia/Yekaterinburg"
LOG_DIR = BASE_DIR / "logs"
LLM_USAGE_LOG_PATH = LOG_DIR / "llm_usage.md"
CHINA_DAILY_STATS_PATH = BASE_DIR / "china_monitor" / "logs" / "daily_stats.md"
ERROR_TRACEBACK_CHARS = int(os.environ.get("ERROR_TRACEBACK_CHARS") or "2800")
SCHEDULER = None
COMMAND_RATE_LIMITER = RateLimiter(
    requests=RATE_LIMIT_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    enabled=RATE_LIMIT_ENABLED,
)

IMAGE_SEARCHER = PreciseImageSearch(download_dir=STORY_DIR) if PreciseImageSearch else None
MEDIA_STATUS_MANAGER = MediaStatusManager() if MediaStatusManager else None
HISTORY_DB = HistoryDB(DB_PATH) if HistoryDB else None

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def redact_secrets(value: str) -> str:
    return redact_secret_values(
        value,
        (
            BOT_TOKEN,
            OPENAI_API_KEY,
            KIMI_API_KEY,
            OPENROUTER_API_KEY,
            GEMINI_API_KEY,
            GROQ_API_KEY,
        ),
    )


def is_owner(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    return OWNER_CHAT_ID in {chat_id, user_id} or ADMIN_TELEGRAM_ID in {chat_id, user_id}


async def require_owner(update: Update) -> bool:
    if is_owner(update):
        return True
    target = update.callback_query.message if update.callback_query and update.callback_query.message else update.message
    if target:
        await target.reply_text("Команда доступна только владельцу.")
    return False


async def enforce_rate_limit(update: Update) -> bool:
    if should_bypass_rate_limit(is_owner(update), RATE_LIMIT_OWNER_BYPASS):
        return True
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    key = str(user_id or chat_id or "unknown")
    if COMMAND_RATE_LIMITER.allow(key):
        return True
    target = update.callback_query.message if update.callback_query and update.callback_query.message else update.message
    if target:
        await target.reply_text("Слишком много команд. Попробуйте позже.")
    return False


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Notify the owner when the polling bot hits an unhandled exception."""
    error = context.error
    logging.exception("Unhandled Telegram bot error", exc_info=error)
    if not ERROR_NOTIFICATIONS_ENABLED or not ADMIN_TELEGRAM_ID:
        return

    error_name = type(error).__name__ if error else "UnknownError"
    error_text = redact_secrets(str(error) if error else "")
    module_name = getattr(getattr(error, "__traceback__", None), "tb_frame", None)
    module_name = module_name.f_globals.get("__name__", "bot") if module_name else "bot"
    now_text = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    trace = ""
    if error:
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        trace = redact_secrets(trace)[-ERROR_TRACEBACK_CHARS:]

    message = (
        "⚠️ Ошибка Headway Bot\n"
        f"Модуль: {module_name}\n"
        f"Ошибка: {error_name}: {error_text[:700] or 'без текста'}\n"
        f"Время: {now_text}\n"
        "Бот продолжил работу / требуется проверка."
    )
    if trace:
        message += f"\n\nКороткий хвост ошибки:\n{trace}"
    try:
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message[:4096])
    except Exception:
        logging.exception("Could not notify owner about Telegram bot error")


def pending_drafts() -> list[Path]:
    return sorted(DRAFT_DIR.glob("*.pending.json"), key=lambda p: p.stat().st_mtime)


def oldest_pending_draft() -> Path | None:
    drafts = pending_drafts()
    return drafts[0] if drafts else None


def article_key_from_filename(filename: str) -> str | None:
    match = re.match(r"^\d+-([0-9a-f]{24})(?:[-.]|$)", filename)
    return match.group(1) if match else None


def mapped_pending_draft(filename: str) -> Path | None:
    exact = DRAFT_DIR / filename
    if exact.exists():
        return exact

    article_key = article_key_from_filename(filename)
    if not article_key:
        return None

    matches = sorted(
        DRAFT_DIR.glob(f"*-{article_key}*.pending.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def draft_from_reply(update: Update) -> Path | None:
    reply = update.message.reply_to_message if update.message else None
    if not reply:
        return None
    if not REVIEW_MAP_PATH.exists():
        drafts = pending_drafts()
        return drafts[0] if len(drafts) == 1 else None
    try:
        mapping = json.loads(REVIEW_MAP_PATH.read_text(encoding="utf-8"))
        filename = mapping.get(str(reply.message_id))
        if filename:
            path = mapped_pending_draft(filename)
            if path:
                return path
        drafts = pending_drafts()
        return drafts[0] if len(drafts) == 1 else None
    except Exception:
        return None


def selected_pending_draft(update: Update) -> Path | None:
    return draft_from_reply(update) or oldest_pending_draft()


def selected_reply_draft(update: Update) -> Path | None:
    return draft_from_reply(update)


def load_draft(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_review_mapping(draft_path: Path, messages) -> None:
    mapping = {}
    if REVIEW_MAP_PATH.exists():
        try:
            mapping = json.loads(REVIEW_MAP_PATH.read_text(encoding="utf-8"))
        except Exception:
            mapping = {}
    for message in messages:
        mapping[str(message.message_id)] = draft_path.name
    mapping[f"draft_id:{draft_callback_id(draft_path)}"] = draft_path.name
    REVIEW_MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


def draft_from_message_id(message_id: int | str) -> Path | None:
    if not REVIEW_MAP_PATH.exists():
        return None
    try:
        mapping = json.loads(REVIEW_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    filename = mapping.get(str(message_id))
    if not filename:
        return None
    return mapped_pending_draft(filename)


def draft_from_callback_id(callback_id: str | None) -> Path | None:
    if not callback_id:
        return None
    if REVIEW_MAP_PATH.exists():
        try:
            mapping = json.loads(REVIEW_MAP_PATH.read_text(encoding="utf-8"))
            filename = mapping.get(f"draft_id:{callback_id}")
            if filename:
                path = mapped_pending_draft(filename)
                if path:
                    return path
        except Exception:
            pass
    matches = sorted(
        DRAFT_DIR.glob(f"*-{callback_id}*.pending.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return matches[0]
    for draft_path in pending_drafts():
        if draft_callback_id(draft_path) == callback_id:
            return draft_path
    return None


def draft_action_keyboard(draft_id: str | Path | None = None) -> InlineKeyboardMarkup:
    return build_draft_action_keyboard(draft_id or "draft")


def append_owner_feedback(update: Update, draft_path: Path | None, text: str) -> None:
    title = ""
    if draft_path and draft_path.exists():
        try:
            title = load_draft(draft_path).get("title") or draft_path.name
        except Exception:
            title = draft_path.name

    line = f"{update.message.date.isoformat()} | {title}: {text.strip()}"
    with GLOBAL_FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    memory = EDITORIAL_MEMORY_PATH.read_text(encoding="utf-8") if EDITORIAL_MEMORY_PATH.exists() else ""
    header = "\n\nOwner feedback memory:\n"
    if "Owner feedback memory:" not in memory:
        memory = memory.rstrip() + header
    memory = memory.rstrip() + f"\n- {text.strip()}"

    lines = memory.splitlines()
    feedback_start = next((i for i, value in enumerate(lines) if value.strip() == "Owner feedback memory:"), None)
    if feedback_start is not None:
        head = lines[: feedback_start + 1]
        items = [line for line in lines[feedback_start + 1 :] if line.strip()]
        lines = head + items[-80:]
        memory = "\n".join(lines).rstrip() + "\n"

    EDITORIAL_MEMORY_PATH.write_text(memory, encoding="utf-8")


def is_reject_feedback(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ["отклони", "отмен", "не публикуй", "в мусор", "плохой пост"])


def is_rewrite_feedback(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ["переделай", "перепиши", "сделай заново", "исправь"])


def is_media_feedback(text: str) -> bool:
    lowered = text.lower()
    media_markers = [
        "фото",
        "фотк",
        "картин",
        "изображ",
        "медиа",
        "дубли",
        "одинаков",
        "зарядк",
        "зарядками",
        "оставь только",
        "убери",
    ]
    return any(marker in lowered for marker in media_markers) and any(
        marker in lowered for marker in ["фото", "фотк", "картин", "изображ", "медиа", "дубли", "одинаков", "оставь только", "зарядк"]
    )


def is_deep_media_search(text: str) -> bool:
    lowered = text.lower().strip()
    return any(marker in lowered for marker in ["ищи", "поищи", "найди картинку", "найди фото", "ищи картинку", "ищи фото"])


def extract_media_search_hint(text: str) -> str:
    hint = clean_visible_text(text)
    hint = re.sub(r"(?i)\b(ищи|поищи|найди|найти|подбери|покажи)\b", " ", hint)
    hint = re.sub(r"(?i)\b(фото|фотку|фотографии|картинку|картинки|изображение|изображения|медиа)\b", " ", hint)
    hint = re.sub(
        r"(?i)\b(станцию|станции|станция|упоминаемую|упомянутую|упоминаемой|в статье|к новости|для поста|эту|это|этой|этого)\b",
        " ",
        hint,
    )
    hint = re.sub(r"\s+", " ", hint).strip(" .,:;!?-—")
    words = re.findall(r"[a-zа-яё0-9\u3400-\u9fff-]{3,}", hint, flags=re.I)
    generic = {"заряд", "зарядка", "зарядную", "charger", "charging", "station", "image", "photo"}
    meaningful = [word for word in words if word.lower() not in generic]
    return hint if meaningful else ""


def is_generate_image_feedback(text: str) -> bool:
    lowered = text.lower().strip()
    return any(marker in lowered for marker in ["сгенерируй", "генерируй", "сделай картинку", "сделай изображение"])


def start_rewrite(draft_path: Path) -> None:
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    subprocess.Popen(
        [str(python_path), str(BASE_DIR / "monitor.py"), "--rewrite-draft", str(draft_path), "--send-review"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def start_media_revision(draft_path: Path, hint_text: str = "") -> None:
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    command = [str(python_path), str(BASE_DIR / "monitor.py"), "--revise-media-draft", str(draft_path), "--send-review"]
    if hint_text:
        command.extend(["--media-hint", hint_text[:500]])
    subprocess.Popen(
        command,
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def start_generated_image(draft_path: Path) -> None:
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    subprocess.Popen(
        [str(python_path), str(BASE_DIR / "monitor.py"), "--generate-image-draft", str(draft_path), "--send-review"],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def split_provider_chain(raw: str) -> list[str]:
    names: list[str] = []
    for item in re.split(r"[,;>\s]+", raw or ""):
        item = item.strip().lower()
        if not item:
            continue
        if item == "free":
            names.extend(["openrouter", "gemini", "groq"])
        else:
            names.append(item)
    return names


def dedupe_provider_names(names: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def provider_setting(name: str):
    if name == "openrouter" and OPENROUTER_API_KEY:
        return OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL), OPENROUTER_MODEL, "openrouter"
    if name == "gemini" and GEMINI_API_KEY:
        return OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL), GEMINI_MODEL, "gemini"
    if name == "groq" and GROQ_API_KEY:
        return OpenAI(api_key=GROQ_API_KEY, base_url=GROQ_BASE_URL), GROQ_MODEL, "groq"
    if name == "kimi" and KIMI_API_KEY:
        return OpenAI(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL), KIMI_MODEL, "kimi"
    if name == "openai" and OPENAI_API_KEY:
        return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL), OPENAI_MODEL, "openai"
    return None


def openai_client():
    if not OPENAI_API_KEY or OpenAI is None:
        return None
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def llm_client():
    if OpenAI is None:
        return None, "", ""
    clients = llm_clients()
    return clients[0] if clients else (None, "", "")


def llm_clients():
    if OpenAI is None:
        return []
    raw_chain = LLM_PROVIDER_CHAIN or LLM_PROVIDER or "openrouter,gemini,groq,kimi,openai"
    clients = []
    for name in dedupe_provider_names(split_provider_chain(raw_chain)):
        setting = provider_setting(name)
        if setting:
            clients.append(setting)
    return clients


def usage_value(usage, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(name) or 0)
    return int(getattr(usage, name, 0) or 0)


def log_llm_usage(provider: str, model: str, phase: str, response) -> None:
    usage = getattr(response, "usage", None)
    total = usage_value(usage, "total_tokens")
    prompt = usage_value(usage, "prompt_tokens")
    completion = usage_value(usage, "completion_tokens")
    if not any((total, prompt, completion)):
        return
    is_new = not LLM_USAGE_LOG_PATH.exists()
    with LLM_USAGE_LOG_PATH.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write("# llm_usage\n\n")
            handle.write("| time | provider | model | phase | input | output | total |\n")
            handle.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        handle.write(
            f"| {time.strftime('%Y-%m-%d %H:%M:%S')} | {provider} | {model} | {phase} | {prompt} | {completion} | {total} |\n"
        )


def clean_visible_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value or "")).strip()


def media_path_for_draft(path: str) -> str:
    media_path = Path(path)
    if not media_path.is_absolute():
        media_path = BASE_DIR / media_path
    return str(media_path.resolve().relative_to(BASE_DIR))


def draft_media_from_precise_result(result: dict) -> list[dict]:
    media = []
    for item in result.get("images") or []:
        path = item.get("path")
        if not path:
            continue
        try:
            media.append(
                {
                    "path": media_path_for_draft(path),
                    "caption": "",
                    "source_query": item.get("source_query") or "",
                    "original_url": item.get("original_url") or "",
                    "entity": item.get("entity") or "",
                }
            )
        except Exception:
            continue
    return media


def precise_media_command_entity(text: str) -> str:
    value = clean_visible_text(text)
    match = re.match(
        r"(?is)^\s*(?:найди|ищи|поищи|подбери)\s+"
        r"(?:фото|фотку|фотографии|картинку|картинки|изображение|изображения|медиа)\s+(.+?)\s*$",
        value,
    )
    if not match:
        return ""
    entity = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;!?\"'")
    generic = {"заряд", "зарядка", "зарядную", "станцию", "станции", "charger", "charging", "station", "photo", "image"}
    meaningful = [word for word in re.findall(r"[a-zа-яё0-9\u3400-\u9fff-]{3,}", entity, flags=re.I) if word.lower() not in generic]
    return entity if meaningful else ""


def is_publish_without_media_command(text: str) -> bool:
    return clean_visible_text(text).lower() in {
        "публикуй без фото",
        "опубликуй без фото",
        "публикуй без картинки",
        "опубликуй без картинки",
        "publish without image",
        "publish without media",
    }


def is_remove_media_command(text: str) -> bool:
    return clean_visible_text(text).lower() in {
        "убери фото",
        "удали фото",
        "без фото",
        "убери картинку",
        "удали картинку",
        "без картинки",
    }


def is_show_media_status_command(text: str) -> bool:
    return clean_visible_text(text).lower() in {
        "статус фото",
        "статус медиа",
        "какое фото",
        "что с фото",
        "что с медиа",
    }


def is_better_media_search_command(text: str) -> bool:
    return clean_visible_text(text).lower() in {
        "найди лучше",
        "найди качественнее",
        "найди альтернативу",
        "ищи лучше",
        "ищи альтернативу",
        "найди еще",
    }


def is_media_control_command(text: str) -> bool:
    return (
        is_publish_without_media_command(text)
        or is_remove_media_command(text)
        or is_show_media_status_command(text)
        or is_better_media_search_command(text)
    )


def extract_entities_from_draft(draft: dict) -> list[str]:
    text = clean_visible_text(
        " ".join(
            [
                draft.get("title") or "",
                draft.get("headline") or "",
                draft.get("lead") or "",
                draft.get("text") or "",
                draft.get("source") or "",
            ]
        )
    )
    known = [
        "Tesla",
        "BYD",
        "NIO",
        "XPeng",
        "Li Auto",
        "Volkswagen",
        "BMW",
        "Mercedes",
        "Kempower",
        "Blink",
        "ChargePoint",
        "Ionity",
        "Fastned",
        "НАРТИС",
        "ФОРА",
        "Россети",
        "Газпром",
        "Сбер",
        "Мосгортранс",
    ]
    entities: list[str] = []
    for brand in known:
        if re.search(rf"(?i)(?<!\w){re.escape(brand)}(?!\w)", text) and brand not in entities:
            entities.append(brand)

    patterns = [
        r"(?<!\w)(?:[A-ZА-ЯЁ]{2,}(?:\s+[A-ZА-ЯЁ0-9-]{2,}){0,2})(?!\w)",
        r"(?<!\w)(?:[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ0-9][a-zа-яё0-9-]+){0,2})(?!\w)",
        r"(?<!\w)(?:[A-ZА-ЯЁ]+[- ]?\d{2,4}[A-ZА-ЯЁ0-9-]*)(?!\w)",
    ]
    stop = {"В", "На", "Для", "Это", "Если", "Источник", "Россия", "Москве", "Петербурге", "Китай"}
    for pattern in patterns:
        for match in re.findall(pattern, text):
            entity = clean_visible_text(match).strip(" .,:;!?\"'")
            if len(entity) < 3 or entity in stop:
                continue
            if entity.lower() in {"новый", "первый", "также", "который", "зарядка", "станция"}:
                continue
            if entity not in entities:
                entities.append(entity)
            if len(entities) >= 5:
                return entities
    return entities[:5]


def article_id_for_draft(draft: dict, draft_path: Path) -> str:
    source = draft.get("link") or draft.get("title") or draft_path.name
    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:24]


def history_payload(draft: dict, draft_path: Path | None = None) -> dict:
    payload = dict(draft)
    if draft_path:
        payload.setdefault("id", article_id_for_draft(draft, draft_path))
    payload.setdefault("headline", payload.get("title") or "")
    payload.setdefault("status", "pending")
    payload.setdefault("region", payload.get("source_region") or "unknown")
    return payload


def history_id(draft: dict, draft_path: Path | None = None) -> str:
    payload = history_payload(draft, draft_path)
    if HISTORY_DB:
        return HISTORY_DB.draft_id(payload, fallback=draft_path.name if draft_path else "")
    return str(payload.get("id") or article_id_for_draft(draft, draft_path or Path("draft")))


def record_history_created(draft: dict, draft_path: Path | None = None) -> str:
    draft_id = history_id(draft, draft_path)
    if not HISTORY_DB:
        return draft_id
    try:
        payload = history_payload(draft, draft_path)
        payload["id"] = draft_id
        HISTORY_DB.record_draft_created(payload, fallback_id=draft_path.name if draft_path else "")
    except Exception:
        logging.exception("Could not record draft creation in history")
    return draft_id


def record_history_feedback(draft: dict, draft_path: Path | None, action: str, feedback: str = "") -> str:
    draft_id = record_history_created(draft, draft_path)
    if not HISTORY_DB:
        return draft_id
    try:
        HISTORY_DB.record_feedback(draft_id, action, feedback, history_payload(draft, draft_path))
    except Exception:
        logging.exception("Could not record owner feedback in history")
    return draft_id


def looks_like_bad_media_feedback(text: str) -> bool:
    lowered = (text or "").lower()
    markers = ["плох", "не то фото", "не та картинка", "дубли", "одинаков", "мусор", "порно", "проституц", "гряз"]
    return any(marker in lowered for marker in markers)


async def send_review_copy(update: Update, context: ContextTypes.DEFAULT_TYPE, draft: dict, draft_path: Path, note: str = "") -> None:
    sent = []
    media = draft.get("media") or []
    if note:
        sent.append(await update.message.reply_text(note))
    if media:
        photos = []
        opened = []
        try:
            for item in media[:10]:
                handle = (BASE_DIR / item["path"]).open("rb")
                opened.append(handle)
                photos.append(InputMediaPhoto(media=handle))
            sent.extend(await context.bot.send_media_group(chat_id=REVIEW_CHAT_ID, media=photos))
        finally:
            for handle in opened:
                handle.close()
    post_text = draft.get("text") or ""
    if post_text:
        sent.append(
            await context.bot.send_message(
                chat_id=REVIEW_CHAT_ID,
                text=post_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        )
    sent.append(
        await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text="Это обновленный черновик с найденными фото. Если ок, ответь на этот черновик: публикуй",
        )
    )
    sent.append(
        await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text="Действия с черновиком:",
            reply_markup=draft_action_keyboard(draft_path),
        )
    )
    save_review_mapping(draft_path, sent)
    record_history_created(draft, draft_path)


def format_media_result(status_result) -> str:
    if MEDIA_STATUS_MANAGER:
        return MEDIA_STATUS_MANAGER.format_status_message(status_result)
    return getattr(status_result, "message", "") or ""


async def remove_media_from_draft(update: Update, draft_path: Path) -> None:
    draft = load_draft(draft_path)
    draft_id = record_history_created(draft, draft_path)
    draft["media"] = []
    draft["media_status"] = "removed_by_owner"
    draft["needs_media"] = False
    draft["media_removed_at"] = int(time.time())
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    if HISTORY_DB:
        try:
            HISTORY_DB.update_media_status(draft_id, "removed_by_owner", has_media=False, draft_data=history_payload(draft, draft_path))
            HISTORY_DB.record_feedback(draft_id, "media_removed", update.message.text or "", history_payload(draft, draft_path))
        except Exception:
            logging.exception("Could not record media removal in history")
    if MEDIA_STATUS_MANAGER:
        result = MEDIA_STATUS_MANAGER.create_removed_result()
        message = MEDIA_STATUS_MANAGER.format_status_message(result)
    else:
        message = "Фото удалено из черновика. Ответьте «публикуй», если отправляем без фото."
    sent = await update.message.reply_text(message)
    save_review_mapping(draft_path, [sent])


async def show_media_status(update: Update, draft_path: Path) -> None:
    draft = load_draft(draft_path)
    record_history_created(draft, draft_path)
    media = draft.get("media") or []
    status = draft.get("media_status") or "not_checked"
    attempts = int(draft.get("media_search_attempts") or 0)
    lines = [
        f"Статус медиа: {status}",
        f"Изображений в черновике: {len(media)}",
        f"Попыток поиска: {attempts}",
    ]
    if media:
        lines.append("")
        lines.append("Файлы:")
        for index, item in enumerate(media[:5], start=1):
            lines.append(f"{index}. {item.get('path') or item}")
    lines.append("")
    lines.append("Команды: «найди фото [модель]», «убери фото», «публикуй без фото».")
    sent = await update.message.reply_text("\n".join(lines))
    save_review_mapping(draft_path, [sent])


async def publish_without_media(update: Update, context: ContextTypes.DEFAULT_TYPE, draft_path: Path) -> None:
    draft = load_draft(draft_path)
    draft_id = record_history_created(draft, draft_path)
    post_text = (draft.get("text") or "").strip()
    if not post_text:
        await update.message.reply_text("Не могу опубликовать без текста поста.")
        return
    draft["media"] = []
    draft["media_status"] = "published_without_media"
    draft["needs_media"] = False
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    if HISTORY_DB:
        try:
            HISTORY_DB.update_media_status(draft_id, "published_without_media", has_media=False, draft_data=history_payload(draft, draft_path))
        except Exception:
            logging.exception("Could not record media status in history")
    if MEDIA_STATUS_MANAGER:
        await update.message.reply_text(MEDIA_STATUS_MANAGER.format_status_message(MEDIA_STATUS_MANAGER.create_published_without_media_result()))
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=post_text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    target = PUBLISHED_DIR / draft_path.name.replace(".pending.json", ".published.json")
    draft_path.rename(target)
    if HISTORY_DB:
        try:
            HISTORY_DB.record_draft_approved(draft_id, feedback="published_without_media", draft_data=history_payload(draft, target))
        except Exception:
            logging.exception("Could not record publish-without-media in history")
    await update.message.reply_text("Опубликовано в канал без изображения.")


async def try_precise_media_search(update: Update, context: ContextTypes.DEFAULT_TYPE, draft_path: Path) -> bool:
    if IMAGE_SEARCHER is None:
        return False
    message_text = update.message.text or ""
    original = load_draft(draft_path)
    draft_history_id = record_history_created(original, draft_path)
    explicit_entity = precise_media_command_entity(message_text)
    entities = [explicit_entity] if explicit_entity else extract_entities_from_draft(original)
    if not entities:
        return False

    draft_id = article_id_for_draft(original, draft_path)
    if explicit_entity:
        await update.message.reply_text(f"Ищу точное фото по запросу: {explicit_entity}")
    result = await asyncio.to_thread(IMAGE_SEARCHER.search_by_entities, entities, draft_id, 2)
    media = draft_media_from_precise_result(result)
    if not media:
        original["media_status"] = "not_found"
        original["media_search_attempts"] = int(original.get("media_search_attempts") or 0) + 1
        draft_path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
        if HISTORY_DB:
            try:
                HISTORY_DB.update_media_status(draft_history_id, "not_found", has_media=False, draft_data=history_payload(original, draft_path))
                HISTORY_DB.record_feedback(draft_history_id, "media_not_found", message_text, history_payload(original, draft_path))
            except Exception:
                logging.exception("Could not record missing media in history")
        if explicit_entity:
            if MEDIA_STATUS_MANAGER:
                status_result = MEDIA_STATUS_MANAGER.create_not_found_result(entities)
                await update.message.reply_text(MEDIA_STATUS_MANAGER.format_status_message(status_result))
            else:
                await update.message.reply_text(result.get("message") or "Точное фото не найдено. Пробую старый глубокий поиск.")
        return False

    target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".precise-media-source.json")
    draft_path.rename(target)
    new_draft = dict(original)
    new_draft_id = f"{draft_id}-precise-media-{int(time.time())}"
    new_draft.update(
        {
            "id": new_draft_id,
            "media": media,
            "media_status": "found_by_precise_search",
            "needs_media": False,
            "generated_media": False,
            "revision": "precise-media",
            "precise_media_entities": entities,
            "precise_media_status": result.get("status"),
            "media_search_attempts": int(original.get("media_search_attempts") or 0) + 1,
            "created_at": int(time.time()),
        }
    )
    new_path = DRAFT_DIR / f"{int(time.time())}-{draft_id}-precise-media.pending.json"
    new_path.write_text(json.dumps(new_draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_history_created(new_draft, new_path)
    if HISTORY_DB:
        try:
            HISTORY_DB.update_media_status(history_id(new_draft, new_path), "found_by_precise_search", has_media=True, draft_data=history_payload(new_draft, new_path))
            HISTORY_DB.record_feedback(draft_history_id, "media_replaced_by_precise_search", message_text, history_payload(original, target))
        except Exception:
            logging.exception("Could not record precise media in history")
    if MEDIA_STATUS_MANAGER:
        status_result = MEDIA_STATUS_MANAGER.create_success_result([item["path"] for item in media])
        note = MEDIA_STATUS_MANAGER.format_status_message(status_result)
    else:
        note = result.get("message") or "Нашел точные фото."
    await send_review_copy(update, context, new_draft, new_path, note=note)
    return True


async def transcribe_voice(update: Update) -> str:
    voice = update.message.voice or update.message.audio
    file = await voice.get_file()
    path = VOICE_DIR / f"voice_{int(time.time())}_{update.message.message_id}.ogg"
    await file.download_to_drive(custom_path=str(path))
    client = openai_client()
    if client is None:
        raise RuntimeError("OpenAI API is not configured for voice transcription")
    with path.open("rb") as fh:
        result = client.audio.transcriptions.create(model=OPENAI_TRANSCRIBE_MODEL, file=fh)
    return clean_visible_text(getattr(result, "text", "") or str(result))


def load_memory_excerpt() -> str:
    parts = []
    if CHANNEL_AGENT_RULES_PATH.exists():
        parts.append(CHANNEL_AGENT_RULES_PATH.read_text(encoding="utf-8", errors="ignore"))
    if EDITORIAL_MEMORY_PATH.exists():
        memory = EDITORIAL_MEMORY_PATH.read_text(encoding="utf-8", errors="ignore")
        if memory.strip():
            parts.append("Recent owner feedback:\n" + memory[-3000:])
    return "\n\n".join(parts)[-10000:]


def generate_owner_voice_draft(transcript: str) -> dict:
    client, model, provider = llm_client()
    if client is None:
        raise RuntimeError("LLM API is not configured for draft generation")
    prompt = f"""
Ты редактор Telegram-канала Headway про электромобили и зарядные станции.
Владелец канала надиктовал идею поста. Сделай авторский полезный пост, а не новость.

Правила канала:
{load_memory_excerpt()}

Задача владельца:
{transcript}

Верни строго JSON:
{{
  "title": "короткий заголовок",
  "telegram_post": "готовый HTML-текст поста для Telegram, 700-1100 символов, без Markdown",
  "story_cards": [
    {{"title": "карточка 1", "body": "короткий текст 1"}},
    {{"title": "карточка 2", "body": "короткий текст 2"}},
    {{"title": "карточка 3", "body": "короткий текст 3"}}
  ]
}}

Пост должен быть практичным: цифры/логика окупаемости/локации/риски/что считать.
Не обещай гарантированный доход. Не пиши источник, если владелец не дал ссылку.
""".strip()
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return valid JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.55 if provider == "kimi" else 0.4,
        "max_tokens": 1200,
    }
    if provider == "openai":
        request["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**request)
    log_llm_usage(provider, model, "owner_voice_draft", response)
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    cards = data.get("story_cards") or []
    if not cards:
        cards = [
            {"title": "Идея", "body": transcript},
            {"title": "Что считать", "body": "Локация, мощность, поток машин, стоимость подключения и обслуживание."},
            {"title": "Риск", "body": "Доход зависит от загрузки станции, тарифа, доступной мощности и сценария стоянки."},
        ]
    return {
        "title": data.get("title") or "Авторская тема",
        "text": data.get("telegram_post") or transcript,
        "cards": cards[:5],
    }


def story_font(size: int, bold: bool = False):
    if ImageFont is None:
        return None
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap_for_card(text: str, width: int, font) -> list[str]:
    words = clean_visible_text(text).split()
    lines = []
    current = ""
    measuring = ImageDraw.Draw(Image.new("RGB", (1, 1))) if Image and ImageDraw else None
    for word in words:
        candidate = f"{current} {word}".strip()
        if measuring and font and measuring.textlength(candidate, font=font) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_story_cards(title: str, cards: list[dict]) -> list[dict]:
    if Image is None or ImageDraw is None:
        return []
    media = []
    title_font = story_font(72, bold=True)
    body_font = story_font(44)
    small_font = story_font(30)
    total = min(len(cards), 5)
    for index, card in enumerate(cards[:5], start=1):
        image = Image.new("RGB", (1080, 1920), "#101820")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1080, 250), fill="#1f6f5f")
        draw.rectangle((0, 1680, 1080, 1920), fill="#f2c94c")
        draw.text((72, 78), "HEADWAY", fill="#ffffff", font=small_font)
        draw.text((72, 145), f"{index}/{total}", fill="#d8fff4", font=small_font)
        card_title = clean_visible_text(card.get("title") or title)
        y = 380
        for line in wrap_for_card(card_title, 920, title_font)[:4]:
            draw.text((72, y), line, fill="#ffffff", font=title_font)
            y += 86
        y += 50
        for line in wrap_for_card(card.get("body") or "", 900, body_font)[:12]:
            draw.text((72, y), line, fill="#e8eef2", font=body_font)
            y += 58
        draw.text((72, 1735), "Зарядные станции: подбор, поставка, проект", fill="#101820", font=small_font)
        path = STORY_DIR / f"voice_story_{int(time.time())}_{index}.jpg"
        image.save(path, format="JPEG", quality=92, optimize=True)
        media.append({"path": str(path.relative_to(BASE_DIR)), "caption": ""})
    return media


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return
    try:
        await update.message.reply_text("Слушаю голосовое и собираю черновик.")
        transcript = await transcribe_voice(update)
        if not transcript:
            await update.message.reply_text("Не смог распознать голосовое. Попробуй сказать чуть короче.")
            return
        idea = generate_owner_voice_draft(transcript)
        media = render_story_cards(idea["title"], idea["cards"])
        draft = {
            "title": idea["title"],
            "text": idea["text"],
            "media": media,
            "source": "owner_voice",
            "link": "",
            "created_at": int(time.time()),
            "owner_voice_transcript": transcript,
            "media_status": "generated_story_cards" if media else "not_found",
            "needs_media": not bool(media),
            "media_source_info": "owner_voice_story_cards" if media else "none",
            "revision": "owner-voice",
        }
        draft_path = DRAFT_DIR / f"{int(time.time())}-owner-voice.pending.json"
        draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        record_history_created(draft, draft_path)

        sent = [await update.message.reply_text(f"Распознал: {transcript[:900]}")]
        if media:
            photos = []
            opened = []
            try:
                for item in media[:10]:
                    handle = (BASE_DIR / item["path"]).open("rb")
                    opened.append(handle)
                    photos.append(InputMediaPhoto(media=handle))
                sent.extend(await context.bot.send_media_group(chat_id=REVIEW_CHAT_ID, media=photos))
            finally:
                for handle in opened:
                    handle.close()
        sent.append(await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=idea["text"],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ))
        sent.append(await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text="Это черновик из голосовой идеи. Если ок, ответь на этот черновик: публикуй",
        ))
        sent.append(await context.bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text="Действия с черновиком:",
            reply_markup=draft_action_keyboard(draft_path),
        ))
        save_review_mapping(draft_path, sent)
    except Exception as exc:
        logging.exception("Voice draft failed")
        await update.message.reply_text(f"Не смог собрать черновик из голосового: {exc}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return

    await update.message.reply_text(
        "Headway bot на связи.\n\n"
        "Я публикую только черновики со статусом pending.\n"
        "Команды:\n"
        "/status - проверить очередь\n"
        "публикуй - опубликовать только черновик, на который ты ответил\n"
        "отмена - отменить только черновик, на который ты ответил\n"
        "голосовое - надиктовать идею поста и карточек для сторис"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return

    drafts = pending_drafts()
    if not drafts:
        await update.message.reply_text("Сейчас нет черновиков на публикацию.")
        return

    lines = [f"В очереди черновиков: {len(drafts)}"]
    for index, draft_path in enumerate(drafts, start=1):
        draft = load_draft(draft_path)
        title = draft.get("title") or draft_path.name
        lines.append(f"{index}. {title}")
    lines.append("")
    lines.append("Для публикации ответь «публикуй» именно на нужный черновик.")
    await update.message.reply_text("\n".join(lines))


def _safe_json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _daily_source_error_count(cutoff: datetime) -> int:
    if not CHINA_DAILY_STATS_PATH.exists():
        return 0
    count = 0
    for line in CHINA_DAILY_STATS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("| 20"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        try:
            row_time = datetime.strptime(cells[0].replace(" UTC", ""), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if row_time < cutoff.astimezone(timezone.utc):
            continue
        errors = cells[4]
        if errors and errors != "-":
            count += len([item for item in errors.split(";") if item.strip()])
    return count


def build_daily_report_text() -> str:
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_ts = int(cutoff_dt.timestamp())
    cutoff_iso = cutoff_dt.isoformat()

    if not DB_PATH.exists():
        return "Статистика за сутки пока не накоплена."

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        article_rows = []
        draft_rows = []
        if "articles" in tables:
            article_rows = conn.execute(
                "SELECT source, status FROM articles WHERE created_at >= ?",
                (cutoff_ts,),
            ).fetchall()
        if "drafts" in tables:
            draft_rows = conn.execute(
                "SELECT source, status, topic_tags FROM drafts WHERE created_at >= ?",
                (cutoff_iso,),
            ).fetchall()

    if not article_rows and not draft_rows:
        return "Статистика за сутки пока не накоплена."

    checked_sources = {str(row["source"] or "unknown") for row in article_rows}
    checked_sources.update(str(row["source"] or "unknown") for row in draft_rows)
    news_found = len(article_rows)
    passed_filter = len([row for row in article_rows if row["status"] in {"drafted", "published"}])
    drafts_created = len(draft_rows)
    rejected = len([row for row in draft_rows if row["status"] in {"rejected", "cancelled"}])
    rejected += len([row for row in article_rows if row["status"] == "rejected"])
    source_errors = _daily_source_error_count(cutoff_dt)

    source_counts: dict[str, int] = {}
    for row in article_rows:
        source = str(row["source"] or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    for row in draft_rows:
        source = str(row["source"] or "unknown")
        source_counts.setdefault(source, 0)

    topic_counts: dict[str, int] = {}
    for row in draft_rows:
        for tag in _safe_json_list(row["topic_tags"]):
            topic = str(tag or "").strip()
            if topic:
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

    lines = [
        "📊 Daily report Headway Bot за 24 часа",
        "",
        f"Источников проверено: {len(checked_sources)}",
        f"Новостей найдено: {news_found}",
        f"Прошло фильтр: {max(passed_filter, drafts_created)}",
        f"Черновиков создано: {drafts_created}",
        f"Отклонено/снято: {rejected}",
        f"Ошибок источников: {source_errors}",
    ]
    if source_counts:
        lines.extend(["", "Топ источников:"])
        for source, count in sorted(source_counts.items(), key=lambda item: item[1], reverse=True)[:5]:
            lines.append(f"- {source}: {count}")
    if topic_counts:
        lines.extend(["", "Топ тем дня:"])
        for topic, count in sorted(topic_counts.items(), key=lambda item: item[1], reverse=True)[:5]:
            lines.append(f"- {topic}: {count}")
    return "\n".join(lines)


async def daily_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID and update.effective_chat.id != ADMIN_TELEGRAM_ID:
        return
    if not await enforce_rate_limit(update):
        return
    await update.message.reply_text(build_daily_report_text(), disable_web_page_preview=True)


async def backup_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID and update.effective_chat.id != ADMIN_TELEGRAM_ID:
        return
    if not await require_owner(update):
        return
    if not await enforce_rate_limit(update):
        return
    result = backup_all_sqlite_databases(DATABASE_DIR, BACKUP_DIR, keep_days=BACKUP_KEEP_DAYS)
    lines = [
        "SQLite backup",
        f"Databases found: {result.found}",
        f"Backups created: {result.created}",
    ]
    if result.paths:
        lines.append("Created:")
        lines.extend(f"- {Path(path).name}" for path in result.paths)
    if result.skipped:
        lines.append("Skipped:")
        lines.extend(f"- {item}" for item in result.skipped)
    await update.message.reply_text("\n".join(lines))


def is_stats_command_text(text: str) -> bool:
    lowered = clean_visible_text(text).lower()
    return (
        lowered in {"статистика", "стата", "stats", "/stats"}
        or lowered.startswith("/stats ")
        or lowered.startswith("статистика ")
        or lowered.startswith("стата ")
        or lowered.startswith("stats ")
    )


def is_update_memory_command_text(text: str) -> bool:
    lowered = clean_visible_text(text).lower()
    return lowered in {"обнови память", "update memory", "генерируй память", "/memory", "/update_memory"}


def stats_days_from_text(text: str) -> int:
    lowered = clean_visible_text(text).lower()
    if "7" in lowered or "недел" in lowered:
        return 7
    if "90" in lowered or "3 месяц" in lowered:
        return 90
    if "365" in lowered or "все" in lowered or "all" in lowered:
        return 365
    return 30


def format_quality_stats(stats: dict) -> str:
    general = stats["general"]
    lines = [
        f"📊 Статистика качества за {stats['period_days']} дней",
        "",
        f"📝 Черновиков: {general['total_drafts']}",
        f"✅ Одобрено: {general['approved']} ({general['approval_rate']}%)",
        f"❌ Отклонено: {general['rejected']}",
        f"🚫 Отменено: {general.get('cancelled', 0)}",
        f"⏳ В ожидании: {general['pending']}",
        f"🖼 С медиа: {general['with_media']}",
        f"⚠️ Плохое медиа: {general['bad_media']}",
    ]
    if stats.get("top_sources"):
        lines.extend(["", "📰 Источники:"])
        for source in stats["top_sources"][:5]:
            lines.append(
                f"• {source['source_name']}: {source['approval_rate']}% "
                f"({source['approved_count']}/{source['total_drafts']})"
            )
    if stats.get("providers"):
        lines.extend(["", "🤖 Провайдеры:"])
        for provider in stats["providers"][:5]:
            lines.append(
                f"• {provider['provider_name']}: {provider['success_rate']}% "
                f"({provider['success_count']}/{provider['total_attempts']})"
            )
    if stats.get("topics"):
        lines.extend(["", "🏷 Темы:"])
        for topic in stats["topics"][:7]:
            lines.append(
                f"• {topic['topic_tag']}: {topic['approval_rate']}% "
                f"({topic['approved_count']}/{topic['total_count']})"
            )
    return "\n".join(lines)


async def show_quality_stats(update: Update, days: int = 30) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return
    if not HISTORY_DB:
        await update.message.reply_text("История решений пока недоступна: модуль HistoryDB не загрузился.")
        return
    try:
        await update.message.reply_text(format_quality_stats(HISTORY_DB.get_statistics(days=days)))
    except Exception as exc:
        logging.exception("Could not show quality stats")
        await update.message.reply_text(f"Не смог собрать статистику: {exc}")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_quality_stats(update, stats_days_from_text(update.message.text or ""))


def build_generated_editorial_memory() -> str:
    if not HISTORY_DB:
        return ""
    stats = HISTORY_DB.get_statistics(days=90)
    feedback = HISTORY_DB.get_recent_feedback(limit=50)
    rejection_reasons: dict[str, int] = {}
    for item in feedback:
        if item.get("action") in {"rejected", "bad_media"} and item.get("feedback_text"):
            reason = clean_visible_text(item["feedback_text"])[:90]
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1

    general = stats["general"]
    lines = [
        "<!-- AUTO_HISTORY_START -->",
        "# Auto Editorial Memory",
        "",
        "Автоматически обновляется на основе решений владельца.",
        f"Последнее обновление: {time.strftime('%Y-%m-%d %H:%M')}",
        "Период анализа: 90 дней",
        "",
        "## Общая статистика",
        "",
        f"- Всего черновиков: {general['total_drafts']}",
        f"- Approval rate: {general['approval_rate']}%",
        f"- С медиа: {general['with_media']}",
        f"- Плохое медиа: {general['bad_media']}",
        "",
        "## Лучшие источники",
        "",
    ]
    good_sources = [source for source in stats.get("top_sources", []) if (source.get("approval_rate") or 0) >= 60]
    lines.extend(
        [f"- **{source['source_name']}**: {source['approval_rate']}% ({source['approved_count']}/{source['total_drafts']})" for source in good_sources]
        or ["- Пока мало данных"]
    )
    lines.extend(["", "## Проблемные источники", ""])
    bad_sources = [source for source in stats.get("problem_sources", []) if (source.get("approval_rate") or 0) < 30 and source.get("total_drafts", 0) >= 3]
    lines.extend(
        [f"- **{source['source_name']}**: {source['approval_rate']}% ({source['approved_count']}/{source['total_drafts']})" for source in bad_sources]
        or ["- Пока мало данных"]
    )
    lines.extend(["", "## Темы, которые хорошо заходят", ""])
    good_topics = [topic for topic in stats.get("topics", []) if (topic.get("approval_rate") or 0) >= 70 and topic.get("total_count", 0) >= 2]
    lines.extend(
        [f"- **{topic['topic_tag']}**: {topic['approval_rate']}% ({topic['approved_count']}/{topic['total_count']})" for topic in good_topics]
        or ["- Пока мало данных"]
    )
    lines.extend(["", "## Частые причины отклонения", ""])
    if rejection_reasons:
        for reason, count in sorted(rejection_reasons.items(), key=lambda item: item[1], reverse=True)[:8]:
            lines.append(f"- {reason} ({count})")
    else:
        lines.append("- Пока мало данных")
    lines.extend(["", "<!-- AUTO_HISTORY_END -->"])
    return "\n".join(lines) + "\n"


async def update_editorial_memory(update: Update) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return
    if not HISTORY_DB:
        await update.message.reply_text("Не могу обновить память: HistoryDB не загрузился.")
        return
    generated = build_generated_editorial_memory()
    existing = EDITORIAL_MEMORY_PATH.read_text(encoding="utf-8") if EDITORIAL_MEMORY_PATH.exists() else "# Headway Telegram Editorial Memory\n"
    start = "<!-- AUTO_HISTORY_START -->"
    end = "<!-- AUTO_HISTORY_END -->"
    if start in existing and end in existing:
        before = existing.split(start, 1)[0].rstrip()
        after = existing.split(end, 1)[1].lstrip()
        content = before + "\n\n" + generated + ("\n" + after if after else "")
    else:
        content = generated + "\n" + existing
    EDITORIAL_MEMORY_PATH.write_text(content.rstrip() + "\n", encoding="utf-8")
    await update.message.reply_text("✅ editorial_memory.md обновлен на основе истории решений.")


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update_editorial_memory(update)


async def cancel_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    draft_path = selected_reply_draft(update)
    if not draft_path:
        await update.message.reply_text("Ответь «отмена» именно на черновик, который нужно снять.")
        return

    draft = load_draft(draft_path)
    draft_id = record_history_created(draft, draft_path)
    target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".cancelled.json")
    draft_path.rename(target)
    if HISTORY_DB:
        try:
            HISTORY_DB.record_draft_cancelled(draft_id, reason=update.message.text or "cancelled_by_owner", draft_data=history_payload(draft, target))
        except Exception:
            logging.exception("Could not record cancellation in history")
    await update.message.reply_text("Черновик отменен.")


async def publish_draft_to_channel(bot, draft_path: Path, feedback: str = "published_by_owner") -> str:
    draft = load_draft(draft_path)
    draft_history_id = record_history_created(draft, draft_path)
    post_text = draft.get("text", "").strip()
    media = draft.get("media", [])
    if not post_text:
        raise ValueError("Draft has no post text")

    published_text_as_caption = False
    opened = []
    try:
        if media and post_text and len(post_text) <= 1024:
            photos = []
            for index, item in enumerate(media[:10]):
                media_path = BASE_DIR / item["path"]
                handle = media_path.open("rb")
                opened.append(handle)
                if index == 0:
                    photos.append(InputMediaPhoto(media=handle, caption=post_text, parse_mode=ParseMode.HTML))
                else:
                    photos.append(InputMediaPhoto(media=handle))
            await bot.send_media_group(chat_id=CHANNEL_ID, media=photos)
            published_text_as_caption = True
        elif media:
            photos = []
            for item in media[:10]:
                media_path = BASE_DIR / item["path"]
                handle = media_path.open("rb")
                opened.append(handle)
                photos.append(InputMediaPhoto(media=handle))
            await bot.send_media_group(chat_id=CHANNEL_ID, media=photos)

        if post_text and not published_text_as_caption:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    finally:
        for handle in opened:
            try:
                handle.close()
            except Exception:
                pass

    target = PUBLISHED_DIR / draft_path.name.replace(".pending.json", ".published.json")
    draft_path.rename(target)
    if HISTORY_DB:
        try:
            HISTORY_DB.record_draft_approved(draft_history_id, feedback=feedback, draft_data=history_payload(draft, target))
            if media:
                HISTORY_DB.update_media_status(draft_history_id, draft.get("media_status") or "published_with_media", has_media=True, draft_data=history_payload(draft, target))
        except Exception:
            logging.exception("Could not record publication in history")
    return "Опубликовано в канал."


async def resend_draft_preview(bot, chat_id: int, draft: dict, draft_path: Path, note: str = "") -> None:
    sent = []
    if note:
        sent.append(await bot.send_message(chat_id=chat_id, text=note))
    media = draft.get("media") or []
    if media:
        photos = []
        opened = []
        try:
            for item in media[:10]:
                handle = (BASE_DIR / item["path"]).open("rb")
                opened.append(handle)
                photos.append(InputMediaPhoto(media=handle))
            sent.extend(await bot.send_media_group(chat_id=chat_id, media=photos))
        finally:
            for handle in opened:
                try:
                    handle.close()
                except Exception:
                    pass
    if draft.get("text"):
        sent.append(await bot.send_message(
            chat_id=chat_id,
            text=draft["text"],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ))
    sent.append(await bot.send_message(
        chat_id=chat_id,
        text="Действия с черновиком:",
        reply_markup=draft_action_keyboard(draft_path),
    ))
    save_review_mapping(draft_path, sent)


async def find_photo_for_draft_from_button(query, context: ContextTypes.DEFAULT_TYPE, draft_path: Path) -> None:
    if IMAGE_SEARCHER is None:
        await query.message.reply_text("Поиск фото сейчас недоступен: модуль поиска не загрузился.")
        return
    draft = load_draft(draft_path)
    draft_history_id = record_history_created(draft, draft_path)
    entities = extract_entities_from_draft(draft)
    if not entities:
        await query.message.reply_text("Не удалось выделить точную сущность для поиска. Ответь текстом: найди фото [модель/компания].")
        return

    await query.message.reply_text(f"Ищу фото по сущностям: {', '.join(entities[:2])}")
    result = await asyncio.to_thread(IMAGE_SEARCHER.search_by_entities, entities, article_id_for_draft(draft, draft_path), 2)
    media = draft_media_from_precise_result(result)
    draft["media_search_attempts"] = int(draft.get("media_search_attempts") or 0) + 1

    if not media:
        draft["media_status"] = "not_found"
        draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
        if HISTORY_DB:
            try:
                HISTORY_DB.update_media_status(draft_history_id, "not_found", has_media=False, draft_data=history_payload(draft, draft_path))
                HISTORY_DB.record_feedback(draft_history_id, "media_not_found_by_button", "inline_find_photo", history_payload(draft, draft_path))
            except Exception:
                logging.exception("Could not record missing media from inline button")
        await query.message.reply_text("Точное фото не найдено. Можно ответить текстом: найди фото [конкретная модель], или нажать генерацию.")
        return

    draft["media"] = media
    draft["media_status"] = "found_by_inline_search"
    draft["needs_media"] = False
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    if HISTORY_DB:
        try:
            HISTORY_DB.update_media_status(draft_history_id, "found_by_inline_search", has_media=True, draft_data=history_payload(draft, draft_path))
            HISTORY_DB.record_feedback(draft_history_id, "media_found_by_button", "inline_find_photo", history_payload(draft, draft_path))
        except Exception:
            logging.exception("Could not record inline media search")
    await resend_draft_preview(context.bot, query.message.chat_id, draft, draft_path, note="Нашел фото и обновил черновик.")


def explain_draft_fit(draft: dict) -> str:
    media = draft.get("media") or []
    text = " ".join(
        str(draft.get(key) or "")
        for key in ("headline", "title", "lead", "summary", "text", "source", "region")
    ).lower()
    keyword_groups = {
        "зарядная инфраструктура": ["заряд", "charging", "charger", "evse", "эзс", "station"],
        "быстрая зарядка": ["fast", "ultra", "dc", "мвт", "квт", "быстр", "ультра"],
        "бизнес локации": ["магазин", "тц", "парков", "hub", "хаб", "fleet", "оператор", "сеть"],
        "Китай / мировые практики": ["china", "byd", "nio", "xpeng", "li auto", "китай"],
        "Россия / применимость": ["росси", "москв", "петербург", "минпромторг", "субсид"],
    }
    triggered = [
        label
        for label, markers in keyword_groups.items()
        if any(marker in text for marker in markers)
    ]
    has_charging = "да" if any(label in triggered for label in ("зарядная инфраструктура", "быстрая зарядка")) else "не очевидно"
    has_business = "да" if any(label in triggered for label in ("бизнес локации", "Россия / применимость")) else "частично"
    lines = [
        "Почему черновик подходит:",
        f"- Источник: {draft.get('source') or 'не указан'}",
        f"- Регион: {draft.get('region') or 'не указан'}",
        f"- Тема: {draft.get('headline') or draft.get('title') or 'не указана'}",
        f"- Сработали темы: {', '.join(triggered[:5]) if triggered else 'явных маркеров мало'}",
        f"- Зарядная инфраструктура: {has_charging}",
        f"- Бизнес-смысл для аудитории: {has_business}",
        f"- Медиа: {'есть' if media else 'нет'}; статус: {draft.get('media_status') or 'not_checked'}",
    ]
    if draft.get("llm_provider"):
        lines.append(f"- Модель/провайдер: {draft.get('llm_provider')} {draft.get('llm_model') or ''}".strip())
    if draft.get("selection_note"):
        lines.append(f"- Отбор: {draft.get('selection_note')}")
    if draft.get("link"):
        lines.append(f"- Оригинал: {draft.get('link')}")
    lines.append("")
    lines.append("Публикация все равно только после явного подтверждения владельца.")
    return "\n".join(lines)


async def draft_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if query.message.chat_id != REVIEW_CHAT_ID:
        await query.answer()
        return
    if not await require_owner(update):
        await query.answer()
        return
    if not await enforce_rate_limit(update):
        await query.answer()
        return
    await query.answer()
    data = query.data or ""
    parts = data.split(":")
    action = parts[0] if parts else ""
    callback_id = parts[1] if len(parts) > 1 else ""
    reason_code = parts[2] if len(parts) > 2 else ""

    # Backward compatibility with buttons from the previous safe stage.
    if action == "draft" and callback_id:
        old_action = callback_id
        action = {
            "publish": "publish",
            "find_photo": "find_photo",
            "generate": "generate_image",
            "rewrite": "rewrite",
            "reject": "reject",
            "why": "why",
        }.get(old_action, old_action)
        callback_id = ""

    draft_path = draft_from_callback_id(callback_id) or draft_from_message_id(query.message.message_id)
    if not draft_path:
        await query.message.reply_text("Не нашел активный черновик для этой кнопки. Возможно, он уже опубликован, отменен или заменен новой версией.")
        return

    try:
        if action == "publish":
            await query.message.reply_text(
                "Опубликовать этот черновик?",
                reply_markup=confirm_publish_keyboard(draft_path),
            )
        elif action == "confirm_publish":
            message = await publish_draft_to_channel(context.bot, draft_path, feedback="published_by_inline_button")
            await query.message.reply_text(message)
        elif action == "cancel":
            await query.message.reply_text("Ок, действие отменено.")
        elif action == "find_photo":
            await find_photo_for_draft_from_button(query, context, draft_path)
        elif action == "generate_image":
            draft = load_draft(draft_path)
            draft_id = record_history_created(draft, draft_path)
            if HISTORY_DB:
                HISTORY_DB.record_feedback(draft_id, "generate_media_requested", "inline_generate", history_payload(draft, draft_path))
            target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".generate-source.json")
            draft_path.rename(target)
            start_generated_image(target)
            await query.message.reply_text("Принял: сгенерирую нейтральную иллюстрацию и пришлю новый черновик на согласование.")
        elif action == "rewrite":
            draft = load_draft(draft_path)
            draft_id = record_history_created(draft, draft_path)
            if HISTORY_DB:
                HISTORY_DB.record_feedback(draft_id, "rewrite_requested", "inline_rewrite", history_payload(draft, draft_path))
            target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".rewrite-source.json")
            draft_path.rename(target)
            start_rewrite(target)
            await query.message.reply_text("Принял: переделаю текст и пришлю новую версию на согласование.")
        elif action == "reject":
            await query.message.reply_text(
                "Выбери причину отклонения:",
                reply_markup=reject_reason_keyboard(draft_path),
            )
        elif action == "reject_reason":
            draft = load_draft(draft_path)
            draft_id = record_history_created(draft, draft_path)
            reason = reject_reason_label(reason_code)
            draft["reject_reason"] = reason
            draft["status"] = "rejected"
            draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".cancelled.json")
            draft_path.rename(target)
            if HISTORY_DB:
                HISTORY_DB.record_draft_rejected(draft_id, reason=reason, draft_data=history_payload(draft, target))
                if reason_code == "bad_media":
                    HISTORY_DB.record_bad_media(draft_id, reason)
            await query.message.reply_text(f"Черновик отклонен. Причина: {reason}")
        elif action == "why":
            await query.message.reply_text(explain_draft_fit(load_draft(draft_path)), disable_web_page_preview=True)
    except Exception as exc:
        logging.exception("Inline draft action failed")
        await query.message.reply_text(f"Не смог выполнить действие: {redact_secrets(str(exc))[:700]}")


def parse_daily_report_time(value: str) -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", value or "")
    if not match:
        return 9, 0
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour, minute


def scheduler_timezone():
    try:
        return ZoneInfo(SCHEDULER_TIMEZONE)
    except Exception:
        return timezone.utc


async def scheduled_daily_report(application: Application) -> None:
    if not DAILY_REPORT_ENABLED:
        return
    try:
        await application.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=build_daily_report_text(),
            disable_web_page_preview=True,
        )
    except Exception:
        logging.exception("Could not send scheduled daily report")


async def run_optional_source_health_check() -> None:
    health_script = BASE_DIR / "source_health_check.py"
    if not health_script.exists():
        return
    python_path = BASE_DIR / ".venv" / "bin" / "python"
    subprocess.Popen(
        [str(python_path), str(health_script)],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


async def scheduled_source_health_check(application: Application) -> None:
    try:
        await run_optional_source_health_check()
    except Exception:
        logging.exception("Could not run source health check")


async def scheduled_history_backup(application: Application) -> None:
    if not BACKUP_ENABLED:
        return
    try:
        result = backup_all_sqlite_databases(DATABASE_DIR, BACKUP_DIR, keep_days=BACKUP_KEEP_DAYS)
        logging.info(
            "Scheduled SQLite backup: found=%s created=%s skipped=%s",
            result.found,
            result.created,
            len(result.skipped),
        )
    except Exception:
        logging.exception("Scheduled history backup failed")


def start_scheduler(application: Application) -> None:
    global SCHEDULER
    if not SCHEDULER_ENABLED:
        return
    if AsyncIOScheduler is None or CronTrigger is None:
        logging.warning("APScheduler is not installed; scheduled jobs are disabled")
        return
    if SCHEDULER and SCHEDULER.running:
        return

    hour, minute = parse_daily_report_time(DAILY_REPORT_TIME)
    SCHEDULER = AsyncIOScheduler(timezone=scheduler_timezone())
    SCHEDULER.add_job(
        scheduled_daily_report,
        CronTrigger(hour=hour, minute=minute, timezone=scheduler_timezone()),
        args=[application],
        id="headway_daily_report",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    SCHEDULER.add_job(
        scheduled_source_health_check,
        CronTrigger(hour="*/6", minute=10, timezone=scheduler_timezone()),
        args=[application],
        id="headway_source_health_check",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    SCHEDULER.add_job(
        scheduled_history_backup,
        CronTrigger(hour=3, minute=20, timezone=scheduler_timezone()),
        args=[application],
        id="headway_history_backup",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    SCHEDULER.start()
    logging.info("APScheduler started: daily report at %02d:%02d %s", hour, minute, SCHEDULER_TIMEZONE)


async def post_init(application: Application) -> None:
    start_scheduler(application)


async def post_shutdown(application: Application) -> None:
    global SCHEDULER
    if SCHEDULER and SCHEDULER.running:
        SCHEDULER.shutdown(wait=False)
    SCHEDULER = None


async def publish_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return
    if not await enforce_rate_limit(update):
        return

    incoming = (update.message.text or "").strip().lower()
    if is_stats_command_text(update.message.text or ""):
        await show_quality_stats(update, stats_days_from_text(update.message.text or ""))
        return
    if is_update_memory_command_text(update.message.text or ""):
        await update_editorial_memory(update)
        return
    if incoming == "отмена":
        if not await require_owner(update):
            return
        await cancel_latest(update, context)
        return

    if incoming not in {"публикуй", "опубликуй", "publish"}:
        action_requires_reply = (
            is_generate_image_feedback(update.message.text)
            or is_deep_media_search(update.message.text)
            or is_media_feedback(update.message.text)
            or is_media_control_command(update.message.text)
            or is_rewrite_feedback(update.message.text)
            or is_reject_feedback(update.message.text)
        )
        if action_requires_reply and not await require_owner(update):
            return
        draft_path = selected_reply_draft(update)
        if action_requires_reply and not draft_path:
            reply = update.message.reply_to_message if update.message else None
            if reply:
                await update.message.reply_text(
                    "Вижу reply, но это сообщение не привязано к текущему черновику. Ответь на свежий черновик, который прислал бот."
                )
                return
            await update.message.reply_text(
                "Ответь командой именно на нужный черновик. Без reply я не буду угадывать, к какому посту применить действие."
            )
            return
        if not draft_path:
            draft_path = selected_pending_draft(update)
        if draft_path and incoming:
            feedback_path = FEEDBACK_DIR / draft_path.name.replace(".pending.json", ".feedback.log")
            with feedback_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{update.message.date.isoformat()} {update.effective_user.id}: {update.message.text}\n")
            append_owner_feedback(update, draft_path, update.message.text)
            current_draft = load_draft(draft_path)
            current_history_id = record_history_feedback(current_draft, draft_path, "owner_feedback", update.message.text or "")
            if looks_like_bad_media_feedback(update.message.text or "") and HISTORY_DB:
                try:
                    HISTORY_DB.record_bad_media(current_history_id, update.message.text or "")
                except Exception:
                    logging.exception("Could not record bad media feedback")
            if is_publish_without_media_command(update.message.text):
                await publish_without_media(update, context, draft_path)
            elif is_remove_media_command(update.message.text):
                await remove_media_from_draft(update, draft_path)
            elif is_show_media_status_command(update.message.text):
                await show_media_status(update, draft_path)
            elif is_generate_image_feedback(update.message.text):
                if HISTORY_DB:
                    try:
                        HISTORY_DB.record_feedback(current_history_id, "generate_media_requested", update.message.text or "", history_payload(current_draft, draft_path))
                    except Exception:
                        logging.exception("Could not record generate media request")
                target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".generate-source.json")
                draft_path.rename(target)
                start_generated_image(target)
                await update.message.reply_text("Понял: сделаю нейтральную иллюстрацию к этому черновику и пришлю новую версию на согласование.")
            elif is_deep_media_search(update.message.text) or is_better_media_search_command(update.message.text) or is_media_feedback(update.message.text):
                if is_deep_media_search(update.message.text) or is_better_media_search_command(update.message.text):
                    try:
                        if await try_precise_media_search(update, context, draft_path):
                            return
                    except Exception:
                        logging.exception("Precise media search failed")
                target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".media-source.json")
                draft_path.rename(target)
                hint = extract_media_search_hint(update.message.text or "")
                start_media_revision(target, hint)
                if hint:
                    await update.message.reply_text(f"Понял: ищу медиа именно по подсказке: {hint}")
                else:
                    await update.message.reply_text("Понял: текст оставляю, ищу изображение глубже и пришлю новый черновик на согласование.")
            elif is_rewrite_feedback(update.message.text):
                if HISTORY_DB:
                    try:
                        HISTORY_DB.record_feedback(current_history_id, "rewrite_requested", update.message.text or "", history_payload(current_draft, draft_path))
                    except Exception:
                        logging.exception("Could not record rewrite request")
                target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".rewrite-source.json")
                draft_path.rename(target)
                start_rewrite(target)
                await update.message.reply_text("Запомнил замечание и делаю новую версию этого черновика.")
            elif is_reject_feedback(update.message.text):
                if HISTORY_DB:
                    try:
                        HISTORY_DB.record_draft_rejected(current_history_id, reason=update.message.text or "", draft_data=history_payload(current_draft, draft_path))
                    except Exception:
                        logging.exception("Could not record rejection in history")
                target = CANCELLED_DIR / draft_path.name.replace(".pending.json", ".cancelled.json")
                draft_path.rename(target)
                await update.message.reply_text("Запомнил обратную связь и снял черновик с публикации.")
            else:
                await update.message.reply_text("Запомнил обратную связь для следующих постов. Черновик не опубликован.")
        return

    if not await require_owner(update):
        return
    draft_path = selected_reply_draft(update)
    if not draft_path:
        await update.message.reply_text("Для публикации ответь «публикуй» именно на нужный черновик.")
        return

    draft = load_draft(draft_path)
    draft_history_id = record_history_created(draft, draft_path)
    post_text = draft.get("text", "").strip()
    media = draft.get("media", [])

    try:
        published_text_as_caption = False
        if media and post_text and len(post_text) <= 1024:
            photos = []
            opened = []

            for index, item in enumerate(media[:10]):
                media_path = BASE_DIR / item["path"]
                handle = media_path.open("rb")
                opened.append(handle)

                if index == 0:
                    photos.append(InputMediaPhoto(media=handle, caption=post_text, parse_mode=ParseMode.HTML))
                else:
                    photos.append(InputMediaPhoto(media=handle))

            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=photos)
            published_text_as_caption = True

            for handle in opened:
                handle.close()
        elif media:
            photos = []
            opened = []

            for index, item in enumerate(media[:10]):
                media_path = BASE_DIR / item["path"]
                handle = media_path.open("rb")
                opened.append(handle)

                if index == 0:
                    photos.append(InputMediaPhoto(media=handle))
                else:
                    photos.append(InputMediaPhoto(media=handle))

            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=photos)

            for handle in opened:
                handle.close()

        if post_text and not published_text_as_caption:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

        target = PUBLISHED_DIR / draft_path.name.replace(".pending.json", ".published.json")
        draft_path.rename(target)
        if HISTORY_DB:
            try:
                HISTORY_DB.record_draft_approved(draft_history_id, feedback="published_by_owner", draft_data=history_payload(draft, target))
                if media:
                    HISTORY_DB.update_media_status(draft_history_id, draft.get("media_status") or "published_with_media", has_media=True, draft_data=history_payload(draft, target))
            except Exception:
                logging.exception("Could not record publication in history")
        await update.message.reply_text("Опубликовано в канал.")
    except Exception as exc:
        logging.exception("Publish failed")
        await update.message.reply_text(f"Ошибка публикации: {exc}")


def main() -> None:
    builder = Application.builder().token(BOT_TOKEN)
    if hasattr(builder, "post_init"):
        builder = builder.post_init(post_init)
    if hasattr(builder, "post_shutdown"):
        builder = builder.post_shutdown(post_shutdown)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("daily_report", daily_report_command))
    app.add_handler(CommandHandler("backup_now", backup_now_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("update_memory", memory_command))
    app.add_handler(
        CallbackQueryHandler(
            draft_button_callback,
            pattern=r"^(draft:|publish:|confirm_publish:|find_photo:|generate_image:|rewrite:|reject:|reject_reason:|why:|cancel:)",
        )
    )
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & ~filters.COMMAND, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, publish_latest))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
