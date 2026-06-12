import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import textwrap
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram import InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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

IMAGE_SEARCHER = PreciseImageSearch(download_dir=STORY_DIR) if PreciseImageSearch else None
MEDIA_STATUS_MANAGER = MediaStatusManager() if MediaStatusManager else None
HISTORY_DB = HistoryDB(DB_PATH) if HistoryDB else None

load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REVIEW_CHAT_ID = int(os.environ["TELEGRAM_REVIEW_CHAT_ID"])
CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
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
LOG_DIR = BASE_DIR / "logs"
LLM_USAGE_LOG_PATH = LOG_DIR / "llm_usage.md"
ERROR_TRACEBACK_CHARS = int(os.environ.get("ERROR_TRACEBACK_CHARS") or "2800")

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def redact_secrets(value: str) -> str:
    text = value or ""
    for secret in (
        BOT_TOKEN,
        OPENAI_API_KEY,
        KIMI_API_KEY,
        OPENROUTER_API_KEY,
        GEMINI_API_KEY,
        GROQ_API_KEY,
    ):
        if secret and len(secret) > 8:
            text = text.replace(secret, f"{secret[:4]}...{secret[-4:]}")
    return text


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Notify the owner when the polling bot hits an unhandled exception."""
    error = context.error
    logging.exception("Unhandled Telegram bot error", exc_info=error)
    if not REVIEW_CHAT_ID:
        return

    error_name = type(error).__name__ if error else "UnknownError"
    error_text = redact_secrets(str(error) if error else "")
    trace = ""
    if error:
        trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        trace = redact_secrets(trace)[-ERROR_TRACEBACK_CHARS:]

    message = (
        "Сбой в Telegram-боте Headway.\n"
        f"Тип: {error_name}\n"
        f"Ошибка: {error_text[:700] or 'без текста'}\n\n"
        "Публикация не выполнялась автоматически. Подробности сохранены в журнале VPS."
    )
    if trace:
        message += f"\n\nКороткий хвост ошибки:\n{trace}"
    try:
        await context.bot.send_message(chat_id=REVIEW_CHAT_ID, text=message[:4096])
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
    REVIEW_MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


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


async def publish_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != REVIEW_CHAT_ID:
        return

    incoming = (update.message.text or "").strip().lower()
    if is_stats_command_text(update.message.text or ""):
        await show_quality_stats(update, stats_days_from_text(update.message.text or ""))
        return
    if is_update_memory_command_text(update.message.text or ""):
        await update_editorial_memory(update)
        return
    if incoming == "отмена":
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
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("memory", memory_command))
    app.add_handler(CommandHandler("update_memory", memory_command))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO) & ~filters.COMMAND, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, publish_latest))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
