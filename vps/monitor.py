import argparse
import asyncio
import base64
import hashlib
import html
import io
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import time
import traceback
import urllib3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from keyboards import draft_action_keyboard as build_draft_action_keyboard

try:
    from PIL import Image
except Exception:
    Image = None

try:
    from parsers.sogou_image_search import search_sogou_images
except Exception:
    search_sogou_images = None

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
MEDIA_DIR = BASE_DIR / "media" / "storage" / "images"
MEDIA_HISTORY_PATH = BASE_DIR / "media" / "media_history.json"
DB_PATH = BASE_DIR / "database" / "history.db"
REVIEW_MAP_PATH = BASE_DIR / "review_map.json"
CHINA_MONITOR_DIR = BASE_DIR / "china_monitor"
CHINA_LOG_DIR = CHINA_MONITOR_DIR / "logs"
CHINA_DAILY_STATS_PATH = CHINA_LOG_DIR / "daily_stats.md"
CHINA_TEST_RUN_PATH = CHINA_LOG_DIR / "test_run.md"
CHINA_TEST_STATE_PATH = CHINA_LOG_DIR / "test_state.json"
LOG_DIR = BASE_DIR / "logs"
LLM_USAGE_LOG_PATH = LOG_DIR / "llm_usage.md"

for folder in (DRAFT_DIR, MEDIA_DIR, DB_PATH.parent, CHINA_LOG_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MEDIA_STATUS_MANAGER = MediaStatusManager() if MediaStatusManager else None
HISTORY_DB = HistoryDB(DB_PATH) if HistoryDB else None

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
REVIEW_CHAT_ID = int(os.environ["TELEGRAM_REVIEW_CHAT_ID"])
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or ""
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None
OPENAI_MODEL = os.environ.get("OPENAI_MODEL") or "gpt-4.1-mini"
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
MONITOR_ERROR_TRACEBACK_CHARS = int(os.environ.get("MONITOR_ERROR_TRACEBACK_CHARS") or "2600")
TRANSLATION_QA_PROVIDER_CHAIN = os.environ.get("TRANSLATION_QA_PROVIDER_CHAIN") or ""
LLM_TEMPLATE_FALLBACK = os.environ.get("LLM_TEMPLATE_FALLBACK", "1").lower() in {"1", "true", "yes"}
TRANSLATION_QA_ENABLED = os.environ.get("TRANSLATION_QA_ENABLED", "1").lower() in {"1", "true", "yes"}
TRANSLATION_QA_MODEL = os.environ.get("TRANSLATION_QA_MODEL") or OPENAI_MODEL or "gpt-4.1-mini"
TRANSLATION_QA_MIN_SCORE = int(os.environ.get("TRANSLATION_QA_MIN_SCORE", "3"))
IMAGE_GENERATION_PROVIDER = os.environ.get("IMAGE_GENERATION_PROVIDER") or "local"
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL") or "gpt-image-1-mini"
OPENAI_IMAGE_SIZE = os.environ.get("OPENAI_IMAGE_SIZE") or "1024x1024"
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY") or "low"
MIN_POST_CHARS = int(os.environ.get("MIN_POST_CHARS") or "420")
EDITORIAL_MEMORY_PATH = BASE_DIR / "editorial_memory.md"
CHANNEL_AGENT_RULES_PATH = BASE_DIR / "channel_agent_rules.md"
ALLOW_RULE_BASED_DRAFTS = os.environ.get("ALLOW_RULE_BASED_DRAFTS", "0").lower() in {"1", "true", "yes"}
CHINA_PROXY_URL = os.environ.get("CHINA_PROXY_URL") or os.environ.get("CHINA_HTTP_PROXY") or ""
CHINA_DIRECT_TIMEOUT = int(os.environ.get("CHINA_DIRECT_TIMEOUT", "20"))
CHINA_SEARCH_QUERIES = [
    item.strip()
    for item in (
        os.environ.get("CHINA_SEARCH_QUERIES")
        or "充电桩;新能源汽车充电;特来电;星星充电;国家电网充电;超充;换电站"
    ).split(";")
    if item.strip()
]
CHINA_WEIBO_QUERIES = [
    item.strip()
    for item in (
        os.environ.get("CHINA_WEIBO_QUERIES")
        or "国家电网 充电桩;BYD 超充;蔚来 换电站;特来电 充电桩"
    ).split(";")
    if item.strip()
]
CHINA_GOOGLE_FALLBACK = os.environ.get("CHINA_GOOGLE_FALLBACK", "").lower() in {"1", "true", "yes"}
ENABLE_DIRECT_CHINA_SEARCH = os.environ.get("ENABLE_DIRECT_CHINA_SEARCH", "1").lower() in {"1", "true", "yes"}
CHINA_DIRECT_FALLBACK = os.environ.get("CHINA_DIRECT_FALLBACK", "1").lower() in {"1", "true", "yes"}
DAILY_MIN_DRAFT = os.environ.get("DAILY_MIN_DRAFT", "1").lower() in {"1", "true", "yes"}
ALLOW_WEB_IMAGE_SEARCH = os.environ.get("ALLOW_WEB_IMAGE_SEARCH", "").lower() in {"1", "true", "yes"}
IMAGE_SEARCH_PROVIDER = (os.environ.get("IMAGE_SEARCH_PROVIDER") or "").strip().lower()
BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY") or ""
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY") or ""
MAX_LOOKBACK_HOURS = int(os.environ.get("MAX_LOOKBACK_HOURS", "168"))
MAX_ARTICLE_AGE_HOURS = int(os.environ.get("MAX_ARTICLE_AGE_HOURS", "72"))
PENDING_EXPIRES_HOURS = int(os.environ.get("PENDING_EXPIRES_HOURS", "24"))
LOOKBACK_WINDOWS = [24, 48, 72, 120, 168]
RUSSIA_SEARCH_QUERIES = [
    item.strip()
    for item in (
        os.environ.get("RUSSIA_SEARCH_QUERIES")
        or "зарядные станции электромобили;электрозарядные станции;зарядные хабы электромобили;субсидии ЭЗС;Минпромторг зарядные станции;Москва зарядки электромобилей;Энергия Москвы зарядки электромобилей;Москва зарядки электромобилей платными;Дептранс зарядки электромобилей;тариф зарядки электромобилей Москва;электромобили Россия CCS2;электробус зарядка"
    ).split(";")
    if item.strip()
]
RUSSIA_TEXT_MARKERS = [
    "росси",
    "москв",
    "санкт-петербург",
    "петербург",
    "кузбасс",
    "кемеров",
    "минпромторг",
    "ростех",
    "россети",
    "мосгортранс",
    "электробус",
    "вэб.рф",
]
FOREIGN_RU_MARKERS = [
    ".co.il",
    ".by",
    ".ua",
    ".kz",
    "israel",
    "израил",
    "украин",
    "беларус",
    "мядел",
    "головне",
    "newsru.co.il",
]

CHINA_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/8.0.47 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Mi 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
]

SOURCES = [
    {"name": "CnEVPost", "url": "https://cnevpost.com/feed/", "region": "China", "weight": 1.2},
    {"name": "CarNewsChina", "url": "https://carnewschina.com/feed/", "region": "China", "weight": 1.1},
    {"name": "Kod.ru", "url": "https://kod.ru/rss", "region": "Russia", "weight": 1.15},
    {"name": "Electrive", "url": "https://www.electrive.com/feed/", "region": "EU/Global", "weight": 1.05},
    {"name": "Charged EVs", "url": "https://chargedevs.com/feed/", "region": "US/Global", "weight": 1.0},
    {
        "name": "CleanTechnica EV",
        "url": "https://cleantechnica.com/category/clean-transport-2/feed/",
        "region": "Global",
        "weight": 1.0,
    },
    {"name": "Electrek", "url": "https://electrek.co/feed/", "region": "US", "weight": 1.0},
    {"name": "InsideEVs", "url": "https://insideevs.com/rss/news/", "region": "US/EU", "weight": 1.0},
]

KEYWORDS = [
    "charging",
    "charger",
    "charge",
    "evse",
    "fast charging",
    "ultra-fast",
    "charging station",
    "battery swap",
    "battery plant",
    "battery factory",
    "subsidy",
    "charging hub",
    "hpc",
    "container",
    "ccs2",
    "gb/t",
    "ocpp",
    "ocpi",
    "power grid",
    "grid",
    "v2g",
    "megawatt",
    "заряд",
    "зарядка",
    "зарядки",
    "зарядная станция",
    "зарядные станции",
    "электрозаряд",
    "эзс",
    "зарядный хаб",
    "зарядные хабы",
    "электромобиль",
    "электромобили",
    "электробус",
    "электробусы",
    "батарея",
    "батареи",
    "субсидия",
    "субсидии",
    "минпромторг",
    "россети",
    "москва",
    "ccs2",
    "gb/t",
    "ocpp",
    "ocpi",
    "充电",
    "充电桩",
    "充电站",
    "充换电",
    "换电",
    "换电站",
    "超充",
    "快充",
    "闪充",
    "动力电池",
    "电池",
    "电动汽车",
    "新能源汽车",
    "特来电",
    "星星充电",
    "国家电网充电",
]

BAD_TEXT_MARKERS = [
    "\u043c\u044b \u0440\u0430\u0434\u044b \u0441\u043e\u043e\u0431\u0449\u0438\u0442\u044c",
    "\u044d\u043a\u043e\u043b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u0438 \u0447\u0438\u0441\u0442",
    "\u043d\u0435\u043e\u0442\u043b\u043e\u0436\u043d\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430",
    "\u0431\u044b\u0442\u044c \u043e\u043f\u0442\u0438\u043c\u0438\u0441\u0442",
    "\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043c\u043e\u0431\u0438\u043b\u0435\u0441\u0442\u0440\u043e",
    "\u0443\u043b\u0443\u0447\u0448\u0435\u043d\u0438\u0435 \u0438\u043d\u0444\u0440\u0430\u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u044b",
    "\u043d\u043e\u0432\u044b\u0435 \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438",
    "\u0443\u0434\u043e\u0431\u043d\u043e \u0438 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e",
    "\u0437\u0430\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435:",
    "\u0432 \u0431\u043e\u0441\u0442\u043e\u043d ",
    "\u0431\u0430\u0439\u0434\u0435\u043d",
    "\u043f\u043e\u043b\u0438\u0442\u0438\u043a",
    "\u043f\u043e\u043b\u0438\u0442\u0438\u0447\u0435\u0441",
    "\u043e\u043f\u043f\u043e\u0437\u0438\u0446",
    "\u0438\u043c\u043f\u043e\u0440\u0442\u043e\u0437\u0430\u043c",
    "\u0438\u043d\u043e\u0441\u0442\u0440\u0430\u043d\u043d",
    "\u0431\u043e\u043b\u0435\u0435 \u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u0438",
    "\u0435\u0449\u0435 \u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c",
    "\u043f\u043e\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u0432\u043e\u0441\u0442\u043e\u0447\u043d\u044b\u0445",
    "\u0432\u043e\u0441\u0442\u043e\u043a\u0435 \u0441\u0448\u0430",
    "\u043d\u0430 \u0432\u043e\u0441\u0442\u043e\u043a\u0435 \u0441\u0448\u0430",
    "\u0443\u0434\u043e\u0431\u0441\u0442\u0432\u043e \u0438 \u0431\u0435\u0437\u043e\u043f\u0430\u0441\u043d\u043e\u0441\u0442\u044c",
    "\u043e\u0442\u043a\u0440\u044b\u0432\u0430\u0435\u0442 \u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\u0438",
    "\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043c\u043e\u0431\u0438\u043b\u0435\u0439\u043d\u044b",
    "\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
    "\u043d\u0435\u0436\u0435\u043b\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u0439 \u043f\u043e\u0434\u0430\u0440\u043e\u043a",
    "\u043b\u0438\u0447\u043d\u0443\u044e \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0443",
    "\u043e\u0442\u0441\u043b\u0435\u0436\u0438\u0432\u0430\u043d\u0438\u0435 \u0430\u0443\u0434\u0438\u0442\u043e\u0440\u0438\u0438",
    "\u043d\u043e\u0432\u043e\u0441\u0442\u044c \u043f\u043e \u0437\u0430\u0440\u044f\u0434\u043d\u043e\u0439 \u0438\u043d\u0444\u0440\u0430\u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0435",
    "\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0441\u043e\u043e\u0431\u0449\u0430\u0435\u0442 \u043e \u0441\u043e\u0431\u044b\u0442\u0438\u0438",
    "\u0434\u0435\u0442\u0430\u043b\u0438 \u043d\u0443\u0436\u043d\u043e \u0441\u0432\u0435\u0440\u0438\u0442\u044c",
    "\u0442\u0435\u043c\u0430 \u043c\u0430\u0442\u0435\u0440\u0438\u0430\u043b\u0430",
    "\u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0438 \u0440\u0435\u0433\u0438\u043e\u043d",
    "\u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0435 \u0446\u0438\u0444\u0440\u044b \u0432 \u0442\u0435\u043a\u0441\u0442\u0435",
    "\u0432 \u0442\u0435\u043a\u0441\u0442\u0435 \u043d\u0435\u0442 \u044f\u0432\u043d\u044b\u0445 \u0446\u0438\u0444\u0440",
    "\u0434\u043b\u044f \u044d\u0437\u0441 \u0432\u0430\u0436\u043d\u043e \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c \u043b\u043e\u043a\u0430\u0446\u0438\u044e",
]

REJECT_TITLE_MARKERS = [
    "92 new ev chargers at 14 charging stations",
    "92 charging ports",
    "92 заряд",
    "kuala lumpur, penang beat evse deployment goals",
    "denza launches n9 flash charging",
    "boston apartment complex comes with 64 ev chargers",
    "hawaii nevi charging station",
    "anker nano",
    "usb c",
    "usb-c",
    "iphone",
    "laptop charger",
    "charging station review",
    "download the guide",
    "whitepaper",
    "opencritic",
]

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]+",
    flags=re.UNICODE,
)

IMAGE_POSITIVE_MARKERS = [
    "charging",
    "charger",
    "chargers",
    "chargepoint",
    "evse",
    "ev-charging",
    "electric-vehicle-charging",
    "charging-station",
    "charging-stations",
    "level-2",
    "level 2",
    "dc-fast",
    "fast-charging",
    "plug",
    "connector",
    "charge-port",
    "wallbox",
    "充电",
    "充电桩",
    "充电站",
    "换电",
    "超充",
    "快充",
    "电动",
    "电池",
    "新能源汽车",
    "заряд",
    "зарядка",
    "зарядная",
    "зарядные",
    "электрозаряд",
    "эзс",
    "электромобиль",
    "электромобили",
    "разъем",
    "разъём",
]

IMAGE_NEGATIVE_MARKERS = [
    "logo",
    "avatar",
    "icon",
    "author",
    "profile",
    "sprite",
    "headshot",
    "portrait",
    "team",
    "apartment",
    "building",
    "rendering",
    "stock",
    "placeholder",
    "banner",
    "adult",
    "porn",
    "porno",
    "sex",
    "xxx",
    "escort",
    "nude",
    "girl",
    "girls",
    "casino",
    "bet",
    "qrcode",
    "qr-code",
    "qr_code",
    "wechat",
    "weixin",
    "miniprogram",
    "mini-program",
    "mini_program",
    "d1ev_pc.jpeg",
    "ghs.png",
    "official wechat",
    "二维码",
    "小程序",
    "扫码",
    "公众号",
    "官方微信",
    "服务号",
    "关注我们",
    "下载app",
    "cleantech tv",
    "cleantechtv",
    "cleantech talk",
    "cleantechtalk",
    "green power green planet",
    "sponsored",
    "advertisement",
    "advertorial",
    "podcast",
    "youtube",
    "save up to",
]


@dataclass
class Candidate:
    title: str
    link: str
    summary: str
    source: str
    region: str
    published: datetime
    score: float


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            create table if not exists articles (
                article_id text primary key,
                title text not null,
                link text not null,
                source text not null,
                status text not null,
                created_at integer not null
            )
            """
        )


def article_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]


def record_draft_history(draft: dict, aid: str = "") -> None:
    if not HISTORY_DB:
        return
    try:
        payload = dict(draft)
        if aid:
            payload.setdefault("id", aid)
        payload.setdefault("headline", payload.get("title") or "")
        payload.setdefault("status", "pending")
        HISTORY_DB.record_draft_created(payload, fallback_id=aid)
    except Exception as exc:
        print(f"[history] Could not record draft: {exc}")


def normalized_title(title: str) -> str:
    title = clean_text(title).lower()
    title = re.sub(r"[^a-zа-яё0-9]+", " ", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip()


STORY_STOPWORDS = {
    "новости",
    "медиа",
    "news",
    "www1",
    "ru",
    "com",
    "для",
    "что",
    "как",
    "это",
    "они",
    "она",
    "его",
    "еще",
    "уже",
    "новую",
    "новые",
    "новая",
    "система",
    "систему",
    "электрозарядные",
    "мощностью",
}


def story_tokens(title: str) -> set[str]:
    normalized = normalized_title(title)
    # Drop the common RSS suffix "headline - source" when it clearly names a publisher.
    parts = normalized.split(" ")
    tokens = []
    for token in parts:
        if len(token) < 3 or token in STORY_STOPWORDS:
            continue
        replacements = {
            "ночной": "ночн",
            "ночная": "ночн",
            "ночную": "ночн",
            "станция": "станция",
            "станцию": "станция",
            "станции": "станция",
            "зарядка": "заряд",
            "зарядки": "заряд",
            "зарядную": "заряд",
            "зарядной": "заряд",
            "зарядных": "заряд",
            "зарядными": "заряд",
            "электромобилей": "электромобиль",
            "электромобили": "электромобиль",
            "электробусам": "электробус",
            "электробусов": "электробус",
            "электробусы": "электробус",
            "электробуса": "электробус",
            "электробус": "электробус",
            "разработали": "разработ",
            "разработал": "разработ",
            "создал": "разработ",
            "представил": "представ",
            "представили": "представ",
            "поставки": "поставк",
            "поставляет": "поставк",
            "начала": "начал",
        }
        tokens.append(replacements.get(token, token))
    return set(tokens)


def cjk_shingles(title: str) -> set[str]:
    compact = "".join(re.findall(r"[\u3400-\u9fff]", title or ""))
    if len(compact) < 4:
        return set()
    return {compact[index : index + 3] for index in range(0, len(compact) - 2)}


def is_same_story(left_title: str, right_title: str) -> bool:
    left_cjk = cjk_shingles(left_title)
    right_cjk = cjk_shingles(right_title)
    if left_cjk and right_cjk:
        shared_cjk = left_cjk & right_cjk
        cjk_ratio = len(shared_cjk) / max(1, min(len(left_cjk), len(right_cjk)))
        if cjk_ratio >= 0.55 or len(shared_cjk) >= 7:
            return True
        left_numbers = set(re.findall(r"\d+(?:\.\d+)?", left_title))
        right_numbers = set(re.findall(r"\d+(?:\.\d+)?", right_title))
        if left_numbers & right_numbers and "充电" in left_title and "充电" in right_title:
            return True

    left = story_tokens(left_title)
    right = story_tokens(right_title)
    if not left or not right:
        return False
    shared = left & right
    if {"ночн", "электробус", "заряд"} <= shared:
        return True
    named = {
        "фора",
        "нартис",
        "ростех",
        "камаз",
        "byd",
        "nio",
        "catl",
        "tesla",
        "kempower",
        "blink",
        "chargepoint",
        "presto",
    }
    if shared & named and len(shared) >= 3:
        return True
    ratio = len(shared) / max(1, min(len(left), len(right)))
    return len(shared) >= 5 and ratio >= 0.55


def dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    unique: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.score, item.published), reverse=True):
        if any(is_same_story(candidate.title, existing.title) for existing in unique):
            continue
        unique.append(candidate)
    return unique


def is_rejected_by_title(title: str) -> bool:
    normalized = normalized_title(title)
    return any(normalized_title(marker) in normalized for marker in REJECT_TITLE_MARKERS)


def is_known(title: str, link: str) -> bool:
    title_key = normalized_title(title)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("select status from articles where article_id = ?", (article_id(link),)).fetchone()
        if row is not None and row[0] not in {"expired", "stale"}:
            return True
        if is_rejected_by_title(title):
            return True
        if title_key:
            rows = conn.execute("select title from articles where status in ('drafted','published','rejected')").fetchall()
            for (stored_title,) in rows:
                stored_key = normalized_title(stored_title)
                if stored_key and (title_key in stored_key or stored_key in title_key):
                    return True
                if is_same_story(title, stored_title):
                    return True
        return False


def mark_article_status_by_link(link: str, status: str) -> None:
    if not link:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "update articles set status = ?, created_at = ? where article_id = ?",
            (status, int(time.time()), article_id(link)),
        )


def expire_stale_pending_drafts() -> int:
    if PENDING_EXPIRES_HOURS <= 0:
        return 0
    cutoff_ts = time.time() - PENDING_EXPIRES_HOURS * 3600
    expired_count = 0
    expired_dir = BASE_DIR / "cancelled"
    expired_dir.mkdir(exist_ok=True)

    for draft_path in DRAFT_DIR.glob("*.pending.json"):
        try:
            stat = draft_path.stat()
        except FileNotFoundError:
            continue
        if stat.st_mtime >= cutoff_ts:
            continue
        try:
            draft = json.loads(draft_path.read_text(encoding="utf-8"))
        except Exception:
            draft = {}
        target = expired_dir / draft_path.name.replace(".pending.json", ".expired.json")
        try:
            shutil.move(str(draft_path), str(target))
            mark_article_status_by_link(draft.get("link") or "", "expired")
            if HISTORY_DB:
                try:
                    draft_id = HISTORY_DB.draft_id(draft, fallback=draft_path.name)
                    HISTORY_DB.record_draft_cancelled(draft_id, reason="expired", draft_data=draft)
                except Exception as exc:
                    print(f"[history] Could not record expired draft {draft_path}: {exc}")
            expired_count += 1
        except Exception as exc:
            print(f"Could not expire pending draft {draft_path}: {exc}")
    if expired_count:
        print(f"Expired stale pending drafts: {expired_count}")
    return expired_count


def is_already_drafted_or_published(link: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "select 1 from articles where article_id = ? and status in ('drafted','published')",
            (article_id(link),),
        ).fetchone()
        return row is not None


def strip_auto_history_memory(memory: str) -> str:
    start = "<!-- AUTO_HISTORY_START -->"
    end = "<!-- AUTO_HISTORY_END -->"
    if start in memory and end in memory:
        before = memory.split(start, 1)[0]
        after = memory.split(end, 1)[1]
        return (before + "\n" + after).strip()
    return memory


def load_editorial_memory() -> str:
    parts: list[str] = []
    if CHANNEL_AGENT_RULES_PATH.exists():
        parts.append(CHANNEL_AGENT_RULES_PATH.read_text(encoding="utf-8"))
    if EDITORIAL_MEMORY_PATH.exists():
        memory = EDITORIAL_MEMORY_PATH.read_text(encoding="utf-8", errors="ignore")
        memory = strip_auto_history_memory(memory)
        if memory.strip():
            parts.append("## Recent Owner Feedback\n\n" + memory[-2500:])
    return "\n\n".join(parts)


def mark_article(candidate: Candidate, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            insert or replace into articles(article_id, title, link, source, status, created_at)
            values (?, ?, ?, ?, ?, ?)
            """,
            (article_id(candidate.link), candidate.title, candidate.link, candidate.source, status, int(time.time())),
        )


def clean_text(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_time(entry) -> datetime:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    return utc_now()


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_article_date_value(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if isinstance(value, list):
        for item in value:
            parsed = parse_article_date_value(item)
            if parsed:
                return parsed
        return None

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_article_date_value(float(text))

    iso_text = text.replace("Z", "+00:00")
    try:
        return normalize_datetime(datetime.fromisoformat(iso_text))
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt)], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    try:
        return normalize_datetime(parsedate_to_datetime(text))
    except Exception:
        return None


def iter_jsonld_nodes(value):
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_jsonld_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_jsonld_nodes(item)


def extract_article_published_time(article_html: str) -> datetime | None:
    if not article_html:
        return None
    soup = BeautifulSoup(article_html, "html.parser")
    selectors = [
        'meta[property="article:published_time"]',
        'meta[name="article:published_time"]',
        'meta[property="og:published_time"]',
        'meta[name="publishdate"]',
        'meta[name="pubdate"]',
        'meta[name="date"]',
        'meta[itemprop="datePublished"]',
        'time[datetime]',
    ]
    for selector in selectors:
        for node in soup.select(selector):
            parsed = parse_article_date_value(node.get("content") or node.get("datetime") or node.get_text(" ", strip=True))
            if parsed:
                return parsed

    for script in soup.select('script[type*="ld+json"]'):
        raw = script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in iter_jsonld_nodes(data):
            parsed = parse_article_date_value(node.get("datePublished") or node.get("dateCreated"))
            if parsed:
                return parsed
    return None


def validate_article_freshness(candidate: Candidate, article_html: str) -> datetime | None:
    cutoff = utc_now() - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    if candidate.published and normalize_datetime(candidate.published) < cutoff:
        raise ValueError(f"Article too old by feed date: {candidate.published.isoformat()}")

    article_published = extract_article_published_time(article_html)
    if article_published and article_published < cutoff:
        raise ValueError(f"Article too old by page date: {article_published.isoformat()}")
    return article_published


def china_headers(referer: str = "") -> dict:
    headers = {
        "User-Agent": random.choice(CHINA_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Connection": "keep-alive",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def china_proxies() -> dict | None:
    if not CHINA_PROXY_URL:
        return None
    return {"http": CHINA_PROXY_URL, "https": CHINA_PROXY_URL}


CHINA_STATS: dict[str, dict] = {}


def reset_china_stats() -> None:
    CHINA_STATS.clear()


def china_source_stats(source: str) -> dict:
    key = source or "China"
    if key not in CHINA_STATS:
        CHINA_STATS[key] = {
            "found": 0,
            "media_success": 0,
            "media_checked": 0,
            "translation_scores": [],
            "errors": [],
        }
    return CHINA_STATS[key]


def china_record_error(source: str, message: str) -> None:
    stats = china_source_stats(source)
    text = clean_text(str(message))[:220]
    if text and text not in stats["errors"]:
        stats["errors"].append(text)


def china_record_media(source: str, success: bool) -> None:
    stats = china_source_stats(source)
    stats["media_checked"] += 1
    if success:
        stats["media_success"] += 1


def china_record_translation(source: str, score: int) -> None:
    stats = china_source_stats(source)
    if score > 0:
        stats["translation_scores"].append(max(1, min(5, int(score))))


def china_error_summary(errors: list[str]) -> str:
    if not errors:
        return "-"
    buckets: dict[str, int] = {}
    for error in errors:
        lowered = error.lower()
        if "502" in lowered:
            key = "502"
        elif "403" in lowered:
            key = "403"
        elif "timeout" in lowered or "timed out" in lowered:
            key = "timeout"
        elif "serpapi" in lowered:
            key = "SerpApi"
        else:
            key = error[:70]
        buckets[key] = buckets.get(key, 0) + 1
    return ", ".join(f"{key} x{count}" for key, count in buckets.items())


def china_get(url: str, referer: str = "", delay: bool = True, source: str = "China") -> str:
    if delay:
        time.sleep(random.uniform(2.0, 5.0))
    try:
        response = requests.get(
            url,
            headers=china_headers(referer),
            proxies=china_proxies(),
            timeout=CHINA_DIRECT_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text
    except Exception as exc:
        china_record_error(source, f"{url} :: {exc}")
        print(f"China fetch failed: {url} :: {exc}")
        return ""


def keyword_score(text: str) -> int:
    haystack = text.lower()
    return sum(1 for keyword in KEYWORDS if keyword.lower() in haystack)


CHANNEL_CORE_MARKERS = [
    "charging",
    "charger",
    "evse",
    "charging station",
    "supercharger",
    "battery swap",
    "bidirectional",
    "v2g",
    "mcs",
    "fleet",
    "electric truck",
    "school bus",
    "electric bus",
    "depot",
    "заряд",
    "эзс",
    "электробус",
    "электрогруз",
    "грузов",
    "тягач",
    "двунаправлен",
    "хаб",
    "подстанц",
    "充电",
    "充电桩",
    "充电站",
    "超充",
    "快充",
    "闪充",
    "换电",
    "换电站",
]

CHANNEL_SECONDARY_MARKERS = [
    "battery",
    "storage",
    "grid",
    "power electronics",
    "sic",
    "mosfet",
    "аккум",
    "батар",
    "накопител",
    "сеть",
    "电池",
    "储能",
    "电网",
]

CHANNEL_NEGATIVE_MARKERS = [
    "amazon",
    "backpack",
    "spacex merger",
    "robotaxi",
    "autonomous driving",
    "self-driving",
    "driver assistance",
    "ferrari",
    "drone",
    "drones",
    "беспилотник",
    "рюкзак",
    "слияние spacex",
    "автономное вождение",
    "儿童书包",
    "书包",
    "亚马逊",
    "无人机",
    "自动驾驶",
    "辅助驾驶",
    "堆叠芯片",
    "芯片封装",
    "无损检测",
]


def is_channel_relevant(candidate: Candidate) -> bool:
    haystack = f"{candidate.title} {candidate.summary} {candidate.source}".lower()
    has_core = any(marker.lower() in haystack for marker in CHANNEL_CORE_MARKERS)
    has_secondary = any(marker.lower() in haystack for marker in CHANNEL_SECONDARY_MARKERS)
    has_negative = any(marker.lower() in haystack for marker in CHANNEL_NEGATIVE_MARKERS)
    if has_negative and not has_core:
        return False
    if has_core:
        return True
    return has_secondary and candidate.score >= 2


def normalize_china_link(url: str) -> str:
    if not url:
        return ""
    url = html.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/"):
        url = urljoin("https://weixin.sogou.com", url)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("url", "target", "u"):
        if query.get(key):
            return unquote(query[key][0])
    return url


def is_valid_source_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_bad_china_match(title: str, summary: str = "") -> bool:
    haystack = f"{title} {summary}".lower()
    bad_markers = ["后援会", "股市", "小白", "视频", "粉丝", "娱乐", "明星", "充电站1", "电动汽车之家", "为新能源汽车而生"]
    if any(marker.lower() in haystack for marker in bad_markers):
        ev_markers = ["汽车", "新能源", "电动", "充电桩", "超充", "换电", "byd", "nio", "特斯拉", "国家能源局", "比亚迪"]
        if any(marker in haystack for marker in ["后援会", "粉丝", "娱乐", "明星", "股市", "小白"]):
            return True
        return not any(marker.lower() in haystack for marker in ev_markers)
    return False


def is_russia_story(title: str, summary: str, source_href: str, source_title: str) -> bool:
    haystack = f"{title} {summary} {source_href} {source_title}".lower()
    if any(marker in haystack for marker in FOREIGN_RU_MARKERS):
        return False

    return any(marker in haystack for marker in RUSSIA_TEXT_MARKERS)


def add_china_candidate(
    candidates: list[Candidate],
    seen_links: set[str],
    title: str,
    link: str,
    summary: str,
    source: str,
    published: datetime,
    weight: float,
) -> None:
    title = clean_text(title)
    summary = clean_text(summary)
    link = normalize_china_link(link)
    if is_bad_china_match(title, summary):
        return
    if not title or not is_valid_source_url(link) or link in seen_links or is_known(title, link):
        return
    score = keyword_score(f"{title} {summary}") * weight
    if score <= 0:
        return
    seen_links.add(link)
    candidates.append(Candidate(title, link, summary, source, "China", published, score))


def google_news_candidates(query: str, source: str, hours: int, weight: float, limit: int = 12) -> list[Candidate]:
    if not CHINA_GOOGLE_FALLBACK:
        return []
    cutoff = utc_now() - timedelta(hours=hours)
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    feed = feedparser.parse(url)
    candidates: list[Candidate] = []
    seen_links: set[str] = set()
    for entry in feed.entries[:limit]:
        title = clean_text(getattr(entry, "title", ""))
        link = getattr(entry, "link", "")
        summary = clean_text(getattr(entry, "summary", ""))
        published = parse_time(entry)
        if published < cutoff:
            continue
        try:
            source_title = clean_text(entry.source.get("title", ""))
            source_href = str(entry.source.get("href", "")).strip()
        except Exception:
            source_title = ""
            source_href = ""
        link = resolve_google_news_article(link, title, source_title, source_href)
        add_china_candidate(candidates, seen_links, title, link, summary, source, published, weight)
    return candidates


def collect_russia_candidates(hours: int) -> list[Candidate]:
    cutoff = utc_now() - timedelta(hours=hours)
    candidates: list[Candidate] = []
    seen_links: set[str] = set()

    for query in RUSSIA_SEARCH_QUERIES:
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ru&gl=RU&ceid=RU:ru"
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            title = clean_text(getattr(entry, "title", ""))
            raw_link = getattr(entry, "link", "")
            summary = clean_text(getattr(entry, "summary", ""))
            published = parse_time(entry)
            if not title or not raw_link or published < cutoff:
                continue

            score = keyword_score(f"{title} {summary}") * 1.25
            if score <= 0:
                continue

            entry_source = ""
            entry_source_href = ""
            try:
                entry_source = clean_text(entry.source.get("title", ""))
                entry_source_href = str(entry.source.get("href", "")).strip()
            except Exception:
                entry_source = ""
                entry_source_href = ""
            if not is_russia_story(title, summary, entry_source_href, entry_source):
                continue
            link = resolve_google_news_article(raw_link, title, entry_source, entry_source_href)
            if link in seen_links or raw_link in seen_links or is_known(title, link) or is_known(title, raw_link):
                continue
            seen_links.add(link)
            candidates.append(Candidate(title, link, summary, entry_source or "Google News RU", "Russia", published, score))

    return candidates


def parse_china_timestamp(value) -> datetime:
    if not value:
        return utc_now()
    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
        else:
            text = str(value).strip()
            timestamp = float(text)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception:
        return utc_now()


def collect_sogou_wechat(hours: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_links: set[str] = set()
    for query in CHINA_SEARCH_QUERIES[:5]:
        url = f"https://weixin.sogou.com/weixin?type=2&query={quote_plus(query)}&ie=utf8&s_from=input"
        page = china_get(url, referer="https://weixin.sogou.com/", source="Sogou WeChat")
        if not page:
            continue
        soup = BeautifulSoup(page, "html.parser")
        nodes = soup.select(".news-list li") or soup.select("li")
        for node in nodes[:10]:
            link_node = node.select_one("h3 a") or node.select_one("a")
            if not link_node:
                continue
            title = link_node.get_text(" ", strip=True)
            link = link_node.get("href") or ""
            summary_node = node.select_one(".txt-info") or node.select_one(".s-p") or node.select_one("p")
            summary = summary_node.get_text(" ", strip=True) if summary_node else ""
            add_china_candidate(candidates, seen_links, title, link, summary, "Sogou WeChat", utc_now(), 1.35)
    return candidates


def collect_d1ev(hours: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_links: set[str] = set()
    # D1EV often blocks search pages, so use both search and public listing pages.
    for list_url in ["https://www.d1ev.com/news", "https://www.d1ev.com/news/qiye", "https://www.d1ev.com/news/jishu"]:
        page = china_get(list_url, referer="https://www.d1ev.com/", source="D1EV")
        if not page:
            continue
        soup = BeautifulSoup(page, "html.parser")
        for link_node in soup.select("a[href*='/news/'], a[href*='/article/']")[:80]:
            title = link_node.get_text(" ", strip=True)
            link = urljoin("https://www.d1ev.com", link_node.get("href") or "")
            if keyword_score(title) <= 0:
                continue
            add_china_candidate(candidates, seen_links, title, link, "", "D1EV", utc_now(), 1.2)

    for query in CHINA_SEARCH_QUERIES[:4]:
        search_url = f"https://www.d1ev.com/search?keyword={quote_plus(query)}"
        page = china_get(search_url, referer="https://www.d1ev.com/", source="D1EV")
        if page:
            soup = BeautifulSoup(page, "html.parser")
            for link_node in soup.select("a[href*='/news/'], a[href*='/article/']")[:12]:
                title = link_node.get_text(" ", strip=True)
                link = urljoin("https://www.d1ev.com", link_node.get("href") or "")
                add_china_candidate(candidates, seen_links, title, link, "", "D1EV", utc_now(), 1.25)
        candidates.extend(google_news_candidates(f"site:d1ev.com {query}", "D1EV", hours, 1.15, limit=8))
    return candidates


def collect_36kr(hours: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_links: set[str] = set()
    try:
        response = requests.post(
            "https://gateway.36kr.com/api/mis/nav/newsflash/flow",
            headers={**china_headers("https://www.36kr.com/newsflashes"), "Content-Type": "application/json"},
            json={
                "partner_id": "web",
                "param": {"siteId": 1, "platformId": 2, "pageSize": 50, "pageEvent": 0},
                "timestamp": int(time.time() * 1000),
            },
            proxies=china_proxies(),
            timeout=CHINA_DIRECT_TIMEOUT,
        )
        if response.ok:
            payload = response.json()
            for item in (payload.get("data") or {}).get("itemList") or []:
                material = item.get("templateMaterial") or {}
                title = material.get("widgetTitle") or ""
                summary = material.get("widgetContent") or ""
                if keyword_score(f"{title} {summary}") <= 0:
                    continue
                item_id = item.get("itemId") or material.get("itemId")
                link = f"https://www.36kr.com/newsflashes/{item_id}" if item_id else ""
                published = parse_china_timestamp(material.get("publishTime") or item.get("publishTime"))
                add_china_candidate(candidates, seen_links, title, link, summary, "36Kr", published, 1.25)
    except Exception as exc:
        china_record_error("36Kr", f"36Kr API failed: {exc}")
        print(f"36Kr API failed: {exc}")

    for query in CHINA_SEARCH_QUERIES[:4]:
        search_url = f"https://www.36kr.com/search/articles/{quote_plus(query)}"
        page = china_get(search_url, referer="https://www.36kr.com/", source="36Kr")
        if page:
            soup = BeautifulSoup(page, "html.parser")
            for link_node in soup.select("a[href*='/p/'], a[href*='/newsflashes/']")[:12]:
                title = link_node.get_text(" ", strip=True)
                link = urljoin("https://www.36kr.com", link_node.get("href") or "")
                summary = ""
                parent = link_node.find_parent(["div", "article", "li"])
                if parent:
                    summary = parent.get_text(" ", strip=True)
                add_china_candidate(candidates, seen_links, title, link, summary, "36Kr", utc_now(), 1.2)
        candidates.extend(google_news_candidates(f"site:36kr.com {query}", "36Kr", hours, 1.1, limit=8))
    return candidates


def collect_weibo(hours: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    for query in CHINA_WEIBO_QUERIES[:4]:
        candidates.extend(google_news_candidates(f"site:weibo.com {query}", "Weibo", hours, 1.0, limit=6))
    return candidates


def serpapi_baidu_search(query: str, source: str = "Baidu SerpApi") -> dict:
    if not SERPAPI_API_KEY:
        return {}
    try:
        response = requests.get(
            "https://serpapi.com/search.json",
            params={
                "engine": "baidu",
                "q": query,
                "api_key": SERPAPI_API_KEY,
                "rn": 20,
            },
            timeout=35,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        china_record_error(source, f"SerpApi Baidu failed for {query}: {exc}")
        print(f"SerpApi Baidu failed: {query} :: {exc}")
        return {}


def add_baidu_results(candidates: list[Candidate], seen_links: set[str], data: dict, source: str, weight: float) -> list[str]:
    followup_queries: list[str] = []
    for section in ("organic_results", "news_results"):
        for item in data.get(section, []) or []:
            title = clean_text(str(item.get("title") or ""))
            link = str(item.get("link") or item.get("displayed_link") or "").strip()
            summary = clean_text(str(item.get("snippet") or item.get("description") or ""))
            if not title or not link:
                continue
            add_china_candidate(candidates, seen_links, title, link, summary, source, utc_now(), weight)

    for item in data.get("top_searches", []) or []:
        text = clean_text(str(item.get("text") or ""))
        if text and keyword_score(text) > 0 and text not in followup_queries:
            followup_queries.append(text)
    return followup_queries


def collect_baidu_serpapi(hours: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen_links: set[str] = set()
    if not SERPAPI_API_KEY:
        return candidates
    followups: list[str] = []
    for query in CHINA_SEARCH_QUERIES[:5]:
        data = serpapi_baidu_search(query)
        followups.extend(add_baidu_results(candidates, seen_links, data, "Baidu SerpApi", 1.25))
    for query in followups[:3]:
        data = serpapi_baidu_search(query)
        add_baidu_results(candidates, seen_links, data, "Baidu Top Searches", 1.15)
    return candidates


def collect_china_candidates(hours: int, force: bool = False) -> list[Candidate]:
    if not (ENABLE_DIRECT_CHINA_SEARCH or force):
        return []
    candidates: list[Candidate] = []
    collectors = [collect_sogou_wechat, collect_d1ev, collect_36kr, collect_weibo]
    if SERPAPI_API_KEY:
        collectors.append(collect_baidu_serpapi)
    for collector in collectors:
        try:
            candidates.extend(collector(hours))
        except Exception as exc:
            china_record_error(collector.__name__.replace("collect_", ""), str(exc))
            print(f"China collector failed: {collector.__name__} :: {exc}")

    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        key = article_id(candidate.link)
        current = unique.get(key)
        if current is None or candidate.score > current.score:
            unique[key] = candidate
    return dedupe_candidates(list(unique.values()))


def collect_candidates(hours: int) -> list[Candidate]:
    cutoff = utc_now() - timedelta(hours=hours)
    candidates: list[Candidate] = []

    for source in SOURCES:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries[:30]:
            title = clean_text(getattr(entry, "title", ""))
            link = getattr(entry, "link", "")
            summary = clean_text(getattr(entry, "summary", ""))
            published = parse_time(entry)
            if not title or not link or published < cutoff or is_known(title, link):
                continue
            score = keyword_score(f"{title} {summary}") * source["weight"]
            if score <= 0:
                continue
            candidates.append(Candidate(title, link, summary, source["name"], source["region"], published, score))

    candidates.extend(collect_russia_candidates(hours))
    candidates.extend(collect_china_candidates(hours))
    return dedupe_candidates(candidates)


def collect_relaxed_candidates(hours: int) -> list[Candidate]:
    cutoff = utc_now() - timedelta(hours=hours)
    candidates: list[Candidate] = []

    for source in SOURCES:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries[:40]:
            title = clean_text(getattr(entry, "title", ""))
            link = getattr(entry, "link", "")
            summary = clean_text(getattr(entry, "summary", ""))
            published = parse_time(entry)
            if not title or not link or published < cutoff or is_already_drafted_or_published(link):
                continue
            if is_rejected_by_title(title):
                continue
            score = keyword_score(f"{title} {summary}") * source["weight"]
            if source["region"] == "China" and score <= 0:
                score = 0.5 * source["weight"]
            if score <= 0:
                continue
            candidates.append(Candidate(title, link, summary, source["name"], source["region"], published, score))

    candidates.extend(collect_russia_candidates(hours))
    return dedupe_candidates(candidates)


def fetch_article_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 HeadwayNewsBot/1.0"}
    try:
        verify = "neftegaz.ru" not in urlparse(url).netloc.lower()
        response = requests.get(url, headers=headers, timeout=20, verify=verify)
        response.raise_for_status()
        return response.text
    except Exception:
        return ""


def is_google_news_link(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower().endswith("news.google.com")


def preferred_source_domain(source_href: str) -> str:
    domain = urlparse(source_href or "").netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def source_domain_from_title(source_title: str) -> str:
    lowered = (source_title or "").lower()
    if "d1ev" in lowered or "第一电动" in source_title:
        return "d1ev.com"
    if "36kr" in lowered or "36氪" in source_title:
        return "36kr.com"
    if "cnevpost" in lowered:
        return "cnevpost.com"
    if "vse42" in lowered:
        return "vse42.ru"
    if "gazeta" in lowered:
        return "gazeta.ru"
    return ""


def resolve_google_news_article(link: str, title: str, source_title: str = "", source_href: str = "") -> str:
    if not is_google_news_link(link):
        return link

    domain = preferred_source_domain(source_href) or source_domain_from_title(source_title)
    queries = []
    if domain:
        queries.append(f'"{title}" site:{domain}')
    if source_title:
        queries.append(f'"{title}" {source_title}')
    queries.append(f'"{title}"')

    for query in queries:
        for result in bing_web_result_links(query, limit=6):
            parsed = urlparse(result)
            result_domain = parsed.netloc.lower()
            if "news.google." in result_domain:
                continue
            if domain and domain not in result_domain:
                continue
            return result

    return link


def extract_article_text(article_html: str, limit: int = 3500) -> str:
    if not article_html:
        return ""
    soup = BeautifulSoup(article_html, "html.parser")
    article = soup.select_one("article") or soup
    parts = [clean_text(node.get_text(" ", strip=True)) for node in article.select("p, li")]
    return "\n".join(part for part in parts if part)[:limit]


def text_from_html_fragment(value: str) -> str:
    if not value:
        return ""
    return clean_text(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))


def image_relevance_score(url: str, context: str = "") -> int:
    haystack = f"{url} {context}".lower()
    score = 0
    for marker in IMAGE_POSITIVE_MARKERS:
        if marker in haystack:
            score += 3 if marker in url.lower() else 2
    for marker in IMAGE_NEGATIVE_MARKERS:
        if marker in haystack:
            score -= 4
    return score


def image_url_from_value(value: str, base_url: str = "") -> str:
    value = html.unescape((value or "").strip().strip("\"'"))
    if not value or value.startswith("data:"):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if base_url:
        value = urljoin(base_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def image_value_from_node(node) -> str:
    for attr in ("srcset", "data-srcset"):
        srcset = node.get(attr)
        if srcset:
            return srcset.split(",")[-1].strip().split(" ")[0]
    for attr in ("src", "data-src", "data-lazy-src", "data-original", "data-image", "data-url", "data-hi-res-src"):
        value = node.get(attr)
        if value:
            return value
    return ""


def image_candidates_from_html(article_html: str, base_url: str = "") -> list[str]:
    soup = BeautifulSoup(article_html, "html.parser")
    scored_urls: list[tuple[int, str]] = []

    for selector, attr in [
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[itemprop="image"]', "content"),
        ('link[rel="image_src"]', "href"),
    ]:
        for node in soup.select(selector):
            value = image_url_from_value(node.get(attr) or "", base_url)
            if value:
                score = image_relevance_score(value)
                if score > 0:
                    scored_urls.append((score, value))

    for node in soup.select("article img, main img, figure img, img[alt]"):
        value = image_url_from_value(image_value_from_node(node), base_url)
        if value:
            context_parts = [
                node.get("alt") or "",
                node.get("title") or "",
                " ".join(node.get("class") or []),
            ]
            figure = node.find_parent("figure")
            if figure:
                caption = figure.select_one("figcaption")
                if caption:
                    context_parts.append(caption.get_text(" ", strip=True))
            score = image_relevance_score(value, " ".join(context_parts))
            if score > 0:
                scored_urls.append((score, value))

    unique = []
    for _, value in sorted(scored_urls, key=lambda item: item[0], reverse=True):
        lowered = value.lower()
        if any(skip in lowered for skip in IMAGE_NEGATIVE_MARKERS):
            continue
        if value not in unique:
            unique.append(value)
    return unique


def raw_image_candidates_from_html(article_html: str, base_url: str = "") -> list[str]:
    soup = BeautifulSoup(article_html, "html.parser")
    urls: list[str] = []

    for selector, attr in [
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[itemprop="image"]', "content"),
        ('link[rel="image_src"]', "href"),
    ]:
        for node in soup.select(selector):
            value = image_url_from_value(node.get(attr) or "", base_url)
            if value and value not in urls:
                urls.append(value)

    for node in soup.select("article img, main img, figure img, img[alt]"):
        value = image_url_from_value(image_value_from_node(node), base_url)
        if not value:
            continue
        context = " ".join(
            [
                node.get("alt") or "",
                node.get("title") or "",
                " ".join(node.get("class") or []),
            ]
        ).lower()
        if any(skip in f"{value} {context}".lower() for skip in IMAGE_NEGATIVE_MARKERS):
            continue
        if value not in urls:
            urls.append(value)

    return urls


def bing_image_search_urls(query: str, limit: int = 12) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 HeadwayNewsBot/1.0"}
    url = f"https://www.bing.com/images/search?q={quote_plus(query)}&qft=+filterui:photo-photo&form=IRFLTR"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        print(f"Bing image search failed: {query} :: {exc}")
        return []

    text = response.text
    urls: list[str] = []
    patterns = [
        r"murl&quot;:&quot;(.*?)&quot;",
        r'"murl":"(.*?)"',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = image_url_from_value(match)
            if value and value not in urls:
                urls.append(value)
                if len(urls) >= limit:
                    return urls
    return urls


def sogou_image_search_urls(query: str, limit: int = 12) -> list[str]:
    if not search_sogou_images or not has_cjk_text(query):
        return []
    try:
        results = search_sogou_images(query, count=limit, save=True, download_limit=min(3, limit))
    except Exception as exc:
        print(f"Sogou image search failed: {query} :: {exc}")
        return []
    urls: list[str] = []
    for item in results:
        image_url = image_url_from_value(item.get("image_url") or item.get("preview") or "")
        if image_url and image_url not in urls and not unsafe_media_text(image_url):
            urls.append(image_url)
    return urls


def google_news_image_search(candidate: Candidate, query: str, limit: int = 8) -> list[str]:
    urls: list[str] = []
    for hl, gl, ceid in [("ru", "RU", "RU:ru"), ("en-US", "US", "US:en")]:
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={hl}&gl={gl}&ceid={ceid}")
        for entry in feed.entries[:8]:
            link = getattr(entry, "link", "")
            if not link or link == candidate.link:
                continue
            html_text = fetch_article_html(link)
            for image_url in image_candidates_from_html(html_text, link) + raw_image_candidates_from_html(html_text, link):
                if image_url not in urls:
                    urls.append(image_url)
                    if len(urls) >= limit:
                        return urls
    return urls


def unsafe_media_text(value: str) -> bool:
    haystack = (value or "").lower()
    return any(marker in haystack for marker in IMAGE_NEGATIVE_MARKERS)


def has_cjk_text(value: str) -> bool:
    return bool(re.search(r"[\u3000-\u30ff\u3400-\u9fff]", value or ""))


def image_search_context_has_ev_media_need(value: str) -> bool:
    haystack = (value or "").lower()
    markers = [
        "заряд",
        "эзс",
        "электро",
        "electro",
        "electric",
        "ev ",
        "charger",
        "charging",
        "station",
        "hub",
        "battery",
        "batter",
        "электробус",
        "автобус",
        "充电",
        "换电",
        "超充",
        "快充",
        "电池",
        "电动",
        "新能源汽车",
    ]
    return any(marker in haystack for marker in markers)


def media_metadata_matches_required(value: str, required_terms: list[str] | None = None) -> bool:
    terms = [term.lower() for term in (required_terms or []) if term and len(term) >= 2]
    if not terms:
        return True
    haystack = (value or "").lower()
    return any(term in haystack for term in terms)


def brave_image_search_urls(query: str, limit: int = 12, required_terms: list[str] | None = None) -> list[str]:
    if not BRAVE_SEARCH_API_KEY:
        return []
    params = {
        "q": query,
        "count": min(max(limit, 1), 20),
        "safesearch": "strict",
    }
    if has_cjk_text(query):
        params.update({"country": "CN", "search_lang": "zh-hans"})
    else:
        params.update({"country": "RU", "search_lang": "ru"})
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
    }
    try:
        response = requests.get(
            "https://api.search.brave.com/res/v1/images/search",
            params=params,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"Brave image API failed: {query} :: {exc}")
        return []

    urls: list[str] = []
    for item in data.get("results", []):
        props = item.get("properties") or {}
        thumb = item.get("thumbnail") or {}
        meta_text = " ".join(
            [
                item.get("title") or "",
                item.get("description") or "",
                item.get("url") or "",
                props.get("url") or "",
                props.get("page_url") or "",
            ]
        )
        if not media_metadata_matches_required(meta_text, required_terms):
            continue
        candidates = [
            props.get("url"),
            item.get("url"),
            thumb.get("src"),
            thumb.get("original"),
            item.get("image"),
        ]
        for value in candidates:
            image_url = image_url_from_value(value or "")
            if image_url and image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                break
        if len(urls) >= limit:
            break
    return urls


def serpapi_image_search_urls(query: str, limit: int = 12, required_terms: list[str] | None = None) -> list[str]:
    if not SERPAPI_API_KEY:
        return []
    params = {
        "engine": "google_images",
        "q": query,
        "api_key": SERPAPI_API_KEY,
        "safe": "active",
        "device": "desktop",
        "ijn": 0,
    }
    if has_cjk_text(query):
        params.update({"hl": "zh-cn", "gl": "cn"})
    else:
        params.update({"hl": "ru", "gl": "ru"})
    try:
        response = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"SerpApi image API failed: {query} :: {exc}")
        return []

    urls: list[str] = []
    for item in data.get("images_results", []):
        meta_text = " ".join(
            [
                item.get("title") or "",
                item.get("source") or "",
                item.get("link") or "",
                item.get("original") or "",
            ]
        )
        if not media_metadata_matches_required(meta_text, required_terms):
            continue
        candidates = [
            item.get("original"),
            item.get("image"),
            item.get("thumbnail"),
        ]
        for value in candidates:
            image_url = image_url_from_value(value or "")
            if image_url and image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                break
        if len(urls) >= limit:
            break
    return urls


def image_search_api_urls(query: str, limit: int = 12, required_terms: list[str] | None = None) -> list[str]:
    provider = IMAGE_SEARCH_PROVIDER or "auto"
    if provider in {"off", "none", "disabled"}:
        return []
    if provider == "brave":
        return brave_image_search_urls(query, limit=limit, required_terms=required_terms)
    if provider == "serpapi":
        return serpapi_image_search_urls(query, limit=limit, required_terms=required_terms)

    providers = []
    if has_cjk_text(query):
        providers = [serpapi_image_search_urls, brave_image_search_urls]
    else:
        providers = [brave_image_search_urls, serpapi_image_search_urls]

    urls: list[str] = []
    for provider_fn in providers:
        for image_url in provider_fn(query, limit=limit, required_terms=required_terms):
            if image_url not in urls:
                urls.append(image_url)
            if len(urls) >= limit:
                return urls
    return urls


def meaningful_media_terms(candidate: Candidate, hint: str = "") -> list[str]:
    terms: list[str] = []
    for term in equipment_model_terms(candidate, hint):
        value = clean_text(term)
        if not value or len(value) < 2:
            continue
        if value.lower() in {"заряд", "фото", "image", "photo", "charging", "charger"}:
            continue
        if value not in terms:
            terms.append(value)
    return terms[:5]


def media_required_terms(candidate: Candidate, hint: str = "") -> list[str]:
    haystack = f"{hint} {candidate.title} {candidate.summary} {candidate.source}".lower()
    if "фора" in haystack:
        return ["фора", "заряд"]
    if "нартис" in haystack:
        return ["нартис", "заряд"]
    if re.search(r"[\u3400-\u9fff]", haystack):
        terms: list[str] = []
        for marker in ["比亚迪", "腾势", "蔚来", "特斯拉", "理想", "小鹏", "国家电网", "特来电", "星星充电", "宁德时代"]:
            if marker in f"{hint} {candidate.title} {candidate.summary} {candidate.source}":
                terms.append(marker)
                if len(terms) >= 2:
                    break
        if "充电" in haystack:
            terms.append("充电")
        elif "换电" in haystack:
            terms.append("换电")
        return terms or ["充电"]

    if any(marker in haystack for marker in ["кузбасс", "кемеров", "vse42"]) and any(
        marker in haystack for marker in ["заряд", "эзс", "evse"]
    ):
        return ["кузбасс", "заряд"]
    if "лиаз" in haystack and "электробус" in haystack:
        return ["лиаз", "электробус"]

    focus_terms = meaningful_media_terms(candidate, hint)
    if focus_terms and image_search_context_has_ev_media_need(haystack):
        required = [term.lower() for term in focus_terms if not has_cjk_text(term)]
        if required:
            return required[:2]

    words = re.findall(r"[a-zа-яё0-9]{4,}", haystack, flags=re.I)
    stop = {
        "фото",
        "станц",
        "станции",
        "заряд",
        "зарядки",
        "электромоб",
        "электромобилей",
        "новость",
        "найди",
        "ищи",
        "показали",
        "начала",
        "поставки",
    }
    terms: list[str] = []
    for word in words:
        if any(word.startswith(item) for item in stop):
            continue
        if word not in terms:
            terms.append(word)
        if len(terms) >= 3:
            break
    return terms[:2] or ["заряд"]


def equipment_search_context(candidate: Candidate, hint: str = "") -> str:
    return text_from_html_fragment(f"{hint} {candidate.title} {candidate.summary} {candidate.source}")


def useful_media_hint(hint: str) -> str:
    hint = clean_text(hint)
    hint = re.sub(r"(?i)\b(ищи|поищи|найди|найти|подбери|покажи)\b", " ", hint)
    hint = re.sub(r"(?i)\b(фото|фотку|фотографии|картинку|картинки|изображение|изображения|медиа)\b", " ", hint)
    hint = re.sub(
        r"(?i)\b(станцию|станции|станция|упоминаемую|упомянутую|упоминаемой|в статье|к новости|для поста|эту|это|этой|этого)\b",
        " ",
        hint,
    )
    hint = re.sub(r"\s+", " ", hint).strip(" .,:;!?-—")
    words = re.findall(r"[a-zа-яё0-9-]{3,}", hint.lower(), flags=re.I)
    generic = {
        "ищи",
        "поищи",
        "найди",
        "фото",
        "фотку",
        "картинку",
        "изображение",
        "медиа",
        "станцию",
        "станции",
        "зарядку",
        "зарядной",
        "упоминаемой",
        "статье",
        "эту",
        "этой",
    }
    meaningful = [word for word in words if word not in generic]
    return hint if meaningful else ""


def equipment_model_terms(candidate: Candidate, hint: str = "") -> list[str]:
    context = equipment_search_context(candidate, hint)
    lowered = context.lower()
    terms: list[str] = []

    if "фора" in lowered:
        terms.append("ФОРА")
    if "нартис" in lowered:
        terms.append("НАРТИС")
    brand_aliases = [
        ("лиаз", "ЛиАЗ"),
        ("камаз", "КАМАЗ"),
        ("москвич", "Москвич"),
        ("эволют", "Evolute"),
        ("evolute", "Evolute"),
        ("byd", "BYD"),
        ("denza", "Denza"),
        ("nio", "NIO"),
        ("xpeng", "XPeng"),
        ("li auto", "Li Auto"),
        ("tesla", "Tesla"),
        ("kempower", "Kempower"),
        ("blink", "Blink"),
        ("chargepoint", "ChargePoint"),
        ("alpitronic", "Alpitronic"),
        ("abb", "ABB"),
        ("siemens", "Siemens"),
        ("比亚迪", "比亚迪"),
        ("腾势", "腾势"),
        ("蔚来", "蔚来"),
        ("理想", "理想"),
        ("小鹏", "小鹏"),
        ("特斯拉", "特斯拉"),
        ("特来电", "特来电"),
        ("星星充电", "星星充电"),
        ("国家电网", "国家电网"),
        ("宁德时代", "宁德时代"),
        ("catl", "CATL"),
        ("evogo", "EVOGO"),
        ("chocolate", "Chocolate"),
        ("巧克力", "巧克力换电"),
    ]
    for marker, label in brand_aliases:
        if marker in lowered and label.upper() not in {item.upper() for item in terms}:
            terms.append(label)

    patterns = [
        (r"\bЭЗС[-\s]?DC[-\s]?\d+[A-ZА-ЯЁ0-9-]*\b", re.I),
        (r"\b[A-ZА-ЯЁ]{2,}[-\s]?[A-ZА-ЯЁ]{1,3}[-\s]?\d+[A-ZА-ЯЁ0-9-]*\b", 0),
        (r"\b(?:S|C|С)\d{2,3}\b", 0),
        (r"\b(?:Yuan\s+Plus|Atto\s?3|Denza\s+[A-Z0-9-]+|Li\s+Auto\s+MEGA|NIO\s+[A-Z0-9-]+|XPeng\s+[A-Z0-9-]+|Kempower\s+[A-Z0-9-]+|ChargePoint\s+[A-Z0-9-]+)\b", re.I),
        (r"\b(?:ЛиАЗ|КАМАЗ|ГАЗ|Москвич)[-\s]?\d{3,5}[A-ZА-ЯЁ0-9-]*\b", re.I),
    ]
    for pattern, flags in patterns:
        for match in re.findall(pattern, context, flags=flags):
            value = clean_text(match).replace("  ", " ")
            value_norm = value.upper().replace(" ", "-")
            if value_norm in {"ФОРА-160", "КАМАЗ-52222"}:
                continue
            if value and value.upper() not in {item.upper() for item in terms}:
                terms.append(value)

    if "фора" in lowered and not any("ЭЗС-DC-4M" in item.upper().replace(" ", "-") for item in terms):
        terms.append("ЭЗС-DC-4M")
    if "нартис" in lowered and not any(item.upper() in {"S90", "С90"} for item in terms):
        terms.append("S90")

    compact: list[str] = []
    for term in terms:
        if term and term not in compact:
            compact.append(term)
    return compact[:5]


def primary_model_query(terms: list[str]) -> str:
    if not terms:
        return ""
    companies = {
        "ФОРА",
        "НАРТИС",
        "ЛИАЗ",
        "КАМАЗ",
        "BYD",
        "DENZA",
        "NIO",
        "XPENG",
        "LI AUTO",
        "TESLA",
        "KEMPOWER",
        "BLINK",
        "CHARGEPOINT",
        "ALPITRONIC",
        "ABB",
        "SIEMENS",
        "比亚迪",
        "腾势",
        "蔚来",
        "理想",
        "小鹏",
        "特斯拉",
        "特来电",
        "星星充电",
        "国家电网",
        "宁德时代",
        "CATL",
        "EVOGO",
        "CHOCOLATE",
        "巧克力换电",
    }
    company = next((term for term in terms if term.upper() in companies), "")
    model = next((term for term in terms if term != company), "")
    if company and model:
        return f"{company} {model}"
    return " ".join(terms[:2])


def known_equipment_image_urls(candidate: Candidate, hint: str = "") -> list[str]:
    context = equipment_search_context(candidate, hint).lower()
    urls: list[str] = []
    if "фора" in context or "эзс-dc-4m" in context or "эзс dc 4m" in context:
        urls.append("https://www.rostec.ru/upload/iblock/00f/558j0tewmoc55dtv6tegce2irjut6hre.jpg")
    if "нартис" in context:
        urls.append("https://www.nartis.ru/upload/iblock/f4c/e34kkmonc2ns828ohyr6w0wdp8m8pz1x.png")
    if "лиаз" in context and "электробус" in context:
        urls.append("https://upload.wikimedia.org/wikipedia/commons/thumb/c/cf/LiAZ-6274.jpg/1280px-LiAZ-6274.jpg")
    if any(marker in context for marker in ["宁德时代", "catl", "evogo", "巧克力", "chocolate", "换电站"]):
        urls.extend(
            [
                # CATL official article about Chocolate battery-swap network scale-up.
                "https://www.catl.com/uploads/1/image/public/202512/20251231135101_d8pcrht487.jpg",
                # CnEVPost photo of a CATL EVOGO battery-swap station in real use context.
                "https://cnevpost.com/wp-content/uploads/2022/01/cb2b87bfbe74bce23ba3f1cba20943b2.jpg",
            ]
        )
    return urls


def media_queries(candidate: Candidate, hint: str = "") -> list[str]:
    haystack = f"{hint} {candidate.title} {candidate.summary} {candidate.source}".lower()
    queries: list[str] = []

    hint_query = useful_media_hint(hint)
    model_terms = equipment_model_terms(candidate, hint)
    if model_terms:
        model_query = primary_model_query(model_terms)
        if has_cjk_text(model_query):
            queries.extend(
                [
                    f"{model_query} 图片",
                    f"{model_query} 充电站 图片",
                    f"{model_query} 充电 图片",
                    f"{model_query} 超充 图片",
                ]
            )
        else:
            queries.extend(
                [
                    f'"{model_query}" фото станции',
                    f"{model_query} фото",
                    f"{model_query} зарядная станция",
                    f"{model_query} charging station photo",
                    f"{model_query} EV charger photo",
                ]
            )

    for value in [hint_query, candidate.title.strip()]:
        if value and value not in queries:
            queries.append(value)

    raw_context = f"{hint} {candidate.title} {candidate.summary} {candidate.source}"
    if "比亚迪" in raw_context or "byd" in haystack:
        queries.extend(
            [
                "比亚迪 闪充站 图片",
                "比亚迪 充电站 图片",
                "比亚迪 兆瓦闪充 图片",
                "BYD flash charging station photo",
                "BYD megawatt flash charging station",
            ]
        )
    if "理想" in raw_context or "mega" in haystack:
        queries.extend(
            [
                "理想 MEGA 430kW 充电 图片",
                "Li Auto MEGA 430kW charging photo",
            ]
        )
    if any(marker in raw_context.lower() for marker in ["宁德时代", "catl", "evogo", "巧克力", "chocolate", "换电"]):
        queries.extend(
            [
                "宁德时代 巧克力换电站 图片",
                "CATL Chocolate battery swap station photo",
                "CATL EVOGO battery swap station photo",
                "site:catl.com 巧克力换电站 图片",
                "site:cnevpost.com CATL EVOGO battery swap station",
            ]
        )

    if "фора" in haystack:
        queries.extend(
            [
                '"ФОРА ЭЗС-DC-4M" фото',
                '"ФОРА ЭЗС-DC-4M" зарядная станция',
                'site:rostec.ru ФОРА ночная зарядка электробусов фото',
                "ФОРА Ночная зарядка фото",
                "ФОРА станция ночной зарядки фото",
                "Ростех ФОРА Ночная зарядка электробус",
                "ФОРА зарядная станция электробус",
            ]
        )
    if "нартис" in haystack:
        queries.extend(
            [
                "site:nartis.ru НАРТИС зарядная станция электробус фото",
                "НАРТИС ультрабыстрые зарядные станции электробусы фото",
                "НАРТИС зарядные станции московских электробусов",
                "НАРТИС электробус зарядка фото",
                "НАРТИС ультрабыстрая зарядная станция",
            ]
        )
    if "лиаз" in haystack and "электробус" in haystack:
        queries.extend(
            [
                '"ЛиАЗ-6274" электробус зарядка фото',
                '"ЛиАЗ-6274" ультрабыстрая зарядка',
                "ЛиАЗ электробус ультрабыстрая зарядка фото",
                "ЛиАЗ-6274 на зарядной станции Москва",
                "site:commons.wikimedia.org LiAZ-6274 charging station",
                "site:mos.ru ЛиАЗ электробус зарядка",
            ]
        )

    compact: list[str] = []
    for query in queries:
        query = clean_text(query)
        if query and query not in compact and not unsafe_media_text(query):
            compact.append(query)
    return compact[:8]


def needs_targeted_equipment_media(candidate: Candidate, hint: str = "") -> bool:
    haystack = f"{hint} {candidate.title} {candidate.summary} {candidate.source}".lower()
    if any(marker in haystack for marker in ["фора", "нартис", "лиаз", "электробус", "比亚迪", "byd", "理想", "mega", "充电桩", "闪充站", "超充"]):
        return True
    return bool(meaningful_media_terms(candidate, hint) and image_search_context_has_ev_media_need(haystack))


def bing_web_result_links(query: str, limit: int = 8) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 HeadwayNewsBot/1.0"}
    try:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": query, "mkt": "ru-RU", "setlang": "ru", "adlt": "strict"},
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"Bing web search failed: {query} :: {exc}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: list[str] = []
    for node in soup.select("li.b_algo h2 a, h2 a"):
        href = node.get("href") or ""
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"}:
            continue
        domain = parsed.netloc.lower()
        if any(skip in domain for skip in ["bing.", "microsoft.", "google.", "youtube.", "telegram."]):
            continue
        if unsafe_media_text(href) or href in links:
            continue
        links.append(href)
        if len(links) >= limit:
            break
    return links


def page_matches_media_terms(article_html: str, url: str, required_terms: list[str]) -> bool:
    text = clean_text(article_html)[:8000].lower()
    haystack = f"{url.lower()} {text}"
    if unsafe_media_text(haystack):
        return False
    return all(term.lower() in haystack for term in required_terms)


def targeted_image_search(candidate: Candidate, article_html: str, hint: str = "", limit: int = 10) -> list[str]:
    required_terms = media_required_terms(candidate, hint)
    targeted_equipment = needs_targeted_equipment_media(candidate, hint)
    urls: list[str] = []

    known_urls = []
    for image_url in known_equipment_image_urls(candidate, hint):
        if image_url not in urls and not unsafe_media_text(image_url):
            known_urls.append(image_url)
    if known_urls:
        return known_urls[:limit]

    if not targeted_equipment and page_matches_media_terms(article_html, candidate.link, required_terms):
        for image_url in image_candidates_from_html(article_html, candidate.link) + raw_image_candidates_from_html(article_html, candidate.link):
            if image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                if len(urls) >= limit:
                    return urls

    for query in media_queries(candidate, hint):
        for page_url in bing_web_result_links(query, limit=8):
            html_text = fetch_article_html(page_url)
            if not html_text or not page_matches_media_terms(html_text, page_url, required_terms):
                continue
            for image_url in image_candidates_from_html(html_text, page_url) + raw_image_candidates_from_html(html_text, page_url):
                if image_url not in urls and not unsafe_media_text(image_url):
                    urls.append(image_url)
                    if len(urls) >= limit:
                        return urls

    for query in media_queries(candidate, hint):
        for image_url in sogou_image_search_urls(query, limit=limit * 2):
            if image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                if len(urls) >= limit:
                    return urls

    for query in media_queries(candidate, hint):
        for image_url in image_search_api_urls(query, limit=limit * 2, required_terms=required_terms):
            if image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                if len(urls) >= limit:
                    return urls

    if targeted_equipment and page_matches_media_terms(article_html, candidate.link, required_terms):
        for image_url in image_candidates_from_html(article_html, candidate.link) + raw_image_candidates_from_html(article_html, candidate.link):
            if image_url not in urls and not unsafe_media_text(image_url):
                urls.append(image_url)
                if len(urls) >= limit:
                    return urls
    return urls


def fallback_image_search(candidate: Candidate, limit: int = 5, aggressive: bool = False) -> list[str]:
    if not ALLOW_WEB_IMAGE_SEARCH:
        return []

    queries = [
        candidate.title,
        f"{candidate.title} EV charging",
        f"{candidate.title} зарядная станция электромобиль",
    ]
    if candidate.source:
        queries.append(f"{candidate.source} {candidate.title}")
    if aggressive:
        queries.extend(
            [
                f"\"{candidate.title}\"",
                f"{candidate.title} charger photo",
                f"{candidate.title} charging station photo",
                f"{candidate.title} фото зарядка электромобиль",
            ]
        )

    urls: list[str] = []
    for query in queries:
        search_urls = google_news_image_search(candidate, query, limit=limit * 2)
        if aggressive:
            search_urls.extend(bing_image_search_urls(query, limit=limit * 2))
        for url in search_urls:
            if url not in urls:
                urls.append(url)
                if len(urls) >= limit:
                    return urls
    return urls


def deep_image_candidates(candidate: Candidate, article_html: str, aggressive: bool = False) -> list[str]:
    urls: list[str] = []
    for source_urls in (
        image_candidates_from_html(article_html, candidate.link),
        raw_image_candidates_from_html(article_html, candidate.link),
        fallback_image_search(candidate, limit=10 if aggressive else 5, aggressive=aggressive),
    ):
        for url in source_urls:
            if url not in urls:
                urls.append(url)
    return urls


def is_qr_like_image(content: bytes) -> bool:
    if Image is None:
        return False
    try:
        image = Image.open(io.BytesIO(content)).convert("L")
        width, height = image.size
        if min(width, height) < 90:
            return False
        aspect = max(width, height) / max(1, min(width, height))
        if aspect > 1.35:
            return False

        size = 96
        thumb = image.resize((size, size))
        pixels = list(thumb.getdata())
        total = len(pixels)
        dark_ratio = sum(1 for pixel in pixels if pixel < 70) / total
        light_ratio = sum(1 for pixel in pixels if pixel > 185) / total
        mid_ratio = 1 - dark_ratio - light_ratio
        if dark_ratio < 0.08 or light_ratio < 0.35 or mid_ratio > 0.42:
            return False

        transitions = 0
        samples = 0
        for y in range(0, size, 3):
            row = [1 if thumb.getpixel((x, y)) > 128 else 0 for x in range(size)]
            transitions += sum(1 for left, right in zip(row, row[1:]) if left != right)
            samples += 1
        for x in range(0, size, 3):
            column = [1 if thumb.getpixel((x, y)) > 128 else 0 for y in range(size)]
            transitions += sum(1 for left, right in zip(column, column[1:]) if left != right)
            samples += 1
        return samples > 0 and (transitions / samples) >= 12
    except Exception:
        return False


def is_low_information_image(content: bytes) -> bool:
    if Image is None:
        return False
    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
        width, height = image.size
        if min(width, height) < 80:
            return True
        thumb = image.resize((96, 96))
        pixels = list(thumb.getdata())
        total = len(pixels)
        near_white = sum(1 for r, g, b in pixels if r > 244 and g > 244 and b > 244) / total
        near_black = sum(1 for r, g, b in pixels if r < 18 and g < 18 and b < 18) / total
        if near_white > 0.92 or near_black > 0.92:
            return True

        gray = thumb.convert("L")
        values = list(gray.getdata())
        average = sum(values) / total
        variance = sum((value - average) ** 2 for value in values) / total
        return variance < 18
    except Exception:
        return False


def download_images(urls: list[str], aid: str, limit: int = 5) -> list[dict]:
    media = []
    seen_hashes = set()
    seen_fingerprints = set()
    history = load_media_history()
    history_changed = False
    headers = {"User-Agent": "Mozilla/5.0 HeadwayNewsBot/1.0"}
    for index, url in enumerate(urls, start=1):
        if len(media) >= limit:
            break
        if unsafe_media_text(url):
            continue
        try:
            verify = "neftegaz.ru" not in urlparse(url).netloc.lower()
            response = requests.get(url, headers=headers, timeout=25, verify=verify)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "image" not in content_type or len(response.content) < 20_000:
                continue
            if is_qr_like_image(response.content) or is_low_information_image(response.content):
                continue
            digest = hashlib.sha256(response.content).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            fingerprint = image_fingerprint(response.content)
            if fingerprint:
                if any(hash_distance(fingerprint, existing) <= 12 for existing in seen_fingerprints):
                    continue
                if media_seen_recently(fingerprint, digest, history, aid):
                    continue
                seen_fingerprints.add(fingerprint)
            image_bytes, ext = normalize_downloaded_image(response.content, content_type)
            path = MEDIA_DIR / f"{aid}_{len(media)+1}{ext}"
            path.write_bytes(image_bytes)
            register_media_history(history, aid, path, digest, fingerprint, url)
            history_changed = True
            media.append({"path": str(path.relative_to(BASE_DIR)), "caption": ""})
        except Exception:
            continue
    if history_changed:
        save_media_history(history)
    return media


def normalize_downloaded_image(content: bytes, content_type: str) -> tuple[bytes, str]:
    if Image is None:
        return content, ".jpg"

    try:
        image = Image.open(io.BytesIO(content)).convert("RGB")
        max_side = 1600
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90, optimize=True)
        return output.getvalue(), ".jpg"
    except Exception:
        return content, ".jpg"


def image_fingerprint(content: bytes) -> str | None:
    if Image is None:
        return None
    try:
        image = Image.open(io.BytesIO(content)).convert("L").resize((8, 8))
        pixels = list(image.getdata())
        average = sum(pixels) / len(pixels)
        bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
        return f"{int(bits, 2):016x}"
    except Exception:
        return None


def hash_distance(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return 64


def load_media_history() -> list[dict]:
    if not MEDIA_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(MEDIA_HISTORY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def save_media_history(history: list[dict]) -> None:
    cutoff = int(time.time()) - 14 * 24 * 3600
    compact = [
        item for item in history
        if int(item.get("created_at") or 0) >= cutoff and (item.get("fingerprint") or item.get("digest"))
    ][-1500:]
    MEDIA_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEDIA_HISTORY_PATH.write_text(json.dumps(compact, ensure_ascii=False, indent=2), encoding="utf-8")


def media_seen_recently(fingerprint: str | None, digest: str, history: list[dict], aid: str) -> bool:
    cutoff = int(time.time()) - 7 * 24 * 3600
    for item in history:
        if int(item.get("created_at") or 0) < cutoff:
            continue
        if item.get("aid") == aid:
            continue
        if digest and item.get("digest") == digest:
            return True
        existing = item.get("fingerprint")
        if fingerprint and existing and hash_distance(fingerprint, existing) <= 8:
            return True
    return False


def register_media_history(history: list[dict], aid: str, path: Path, digest: str, fingerprint: str | None, url: str) -> None:
    history.append(
        {
            "created_at": int(time.time()),
            "aid": aid,
            "path": str(path.relative_to(BASE_DIR)),
            "digest": digest,
            "fingerprint": fingerprint or "",
            "url": url[:500],
        }
    )


def generated_image_kind(draft: dict) -> str:
    context = json.dumps(draft, ensure_ascii=False).lower()
    if any(marker in context for marker in ["\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u0431\u0443\u0441", "electrobus", "bus depot"]):
        return "bus"
    if any(marker in context for marker in ["\u0442\u0440\u0430\u0441\u0441", "highway", "roadside", "\u043c\u0435\u0436\u0433\u043e\u0440\u043e\u0434"]):
        return "highway"
    if any(marker in context for marker in ["\u0436\u043a", "\u0436\u0438\u043b", "\u043e\u0444\u0438\u0441", "office", "parking", "\u043f\u0430\u0440\u043a\u043e\u0432"]):
        return "parking"
    if any(marker in context for marker in ["battery", "\u0431\u0430\u0442\u0430\u0440", "\u0430\u043a\u043a\u0443\u043c\u0443\u043b", "gwh", "\u043a\u0432\u0442\u00b7\u0447"]):
        return "battery"
    if any(marker in context for marker in ["byd", "denza", "li auto", "mega", "\u6bd4\u4e9a\u8fea", "\u95ea\u5145", "flash charging", "\u043c\u0435\u0433\u0430\u0432\u0430\u0442"]):
        return "fast"
    if any(marker in context for marker in ["hub", "\u0445\u0430\u0431", "\u043a\u043e\u043d\u0442\u0435\u0439\u043d\u0435\u0440"]):
        return "hub"
    return "charger"


def generated_image_prompt(draft: dict) -> str:
    title = clean_text(draft.get("title") or "")
    text = clean_text(BeautifulSoup(draft.get("text") or "", "html.parser").get_text(" ", strip=True))
    scene = generated_image_kind(draft)
    scene_guidance = {
        "bus": "urban electric bus charging at a depot, pantograph or high-power charger visible",
        "highway": "roadside fast-charging site near a highway service stop",
        "parking": "EV charging points in a residential or office parking area",
        "battery": "battery production or battery-pack technology context, no brand logos",
        "fast": "high-power EV charging equipment with an electric vehicle nearby",
        "hub": "modular EV charging hub with several chargers and clean service infrastructure",
        "charger": "modern EV charging station with visible connector and parked electric vehicle",
    }.get(scene, "modern EV charging station with visible connector and parked electric vehicle")
    return f"""
Create a polished editorial image for a Telegram post about EV charging infrastructure.
Use a realistic but clearly generic scene: {scene_guidance}.
No text, no captions, no logos, no brand marks, no QR codes, no app screens, no license plates, no fake press-event signage.
Do not pretend this is the exact original photo from the news. It should be a neutral illustrative image that helps readers understand the topic.
Clean daylight or well-lit evening, professional EV industry look, useful infrastructure details, natural composition.
News context: {title}. {text[:900]}
""".strip()


def generate_openai_image(draft: dict, aid: str) -> list[dict]:
    if not OPENAI_API_KEY or not OPENAI_BASE_URL:
        return []
    prompt = generated_image_prompt(draft)
    endpoint = f"{OPENAI_BASE_URL.rstrip('/')}/images/generations"
    payload = {
        "model": OPENAI_IMAGE_MODEL,
        "prompt": prompt,
        "size": OPENAI_IMAGE_SIZE,
        "quality": OPENAI_IMAGE_QUALITY,
        "n": 1,
    }
    try:
        response = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        data = response.json()
        image_base64 = (data.get("data") or [{}])[0].get("b64_json") or ""
        if not image_base64:
            return []
        image_bytes = base64.b64decode(image_base64)
        if is_qr_like_image(image_bytes):
            return []
        path = MEDIA_DIR / f"{aid}_generated_openai_{int(time.time())}.png"
        path.write_bytes(image_bytes)
        return [{"path": str(path.relative_to(BASE_DIR)), "caption": ""}]
    except Exception as exc:
        print(f"OpenAI image generation failed: {exc}")
        return []


def draw_generated_car(draw, x: int, y: int, scale: float, color: str) -> None:
    w = int(470 * scale)
    h = int(120 * scale)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=int(40 * scale), fill=color)
    draw.polygon(
        [
            (x + int(70 * scale), y),
            (x + int(160 * scale), y - int(80 * scale)),
            (x + int(330 * scale), y - int(80 * scale)),
            (x + int(425 * scale), y),
        ],
        fill="#36536e",
    )
    for wheel_x in (x + int(95 * scale), x + int(355 * scale)):
        draw.ellipse((wheel_x, y + int(85 * scale), wheel_x + int(90 * scale), y + int(175 * scale)), fill="#111820")
        draw.ellipse((wheel_x + int(25 * scale), y + int(110 * scale), wheel_x + int(65 * scale), y + int(150 * scale)), fill="#edf4fb")


def draw_generated_charger(draw, x: int, y: int, scale: float, accent: str = "#2f7dd1") -> None:
    w = int(135 * scale)
    h = int(300 * scale)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=int(22 * scale), fill="#ffffff", outline=accent, width=max(3, int(7 * scale)))
    draw.rectangle((x + int(35 * scale), y + int(55 * scale), x + int(100 * scale), y + int(105 * scale)), fill="#d9ecff")
    draw.line((x + int(20 * scale), y + int(175 * scale), x - int(95 * scale), y + int(230 * scale)), fill=accent, width=max(4, int(10 * scale)))
    draw.ellipse((x - int(112 * scale), y + int(215 * scale), x - int(72 * scale), y + int(255 * scale)), fill=accent)


def generate_neutral_image(draft: dict, aid: str) -> list[dict]:
    if Image is None:
        raise ValueError("Pillow is not available")
    scene = generated_image_kind(draft)
    seed = int(hashlib.sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    palettes = [
        ("#f4f7fb", "#d8e1ec", "#2f7dd1", "#203040"),
        ("#f6f8f2", "#dbe6d4", "#1f8f72", "#263b34"),
        ("#f7f4ef", "#ded6ca", "#c66a2d", "#2c3544"),
        ("#f3f4f8", "#d7dbe8", "#6a5acd", "#253044"),
    ]
    bg, ground, accent, dark = palettes[seed % len(palettes)]
    image = Image.new("RGB", (1280, 720), bg)

    try:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1280, 720), fill=bg)
        draw.rectangle((70, 560, 1210, 610), fill=ground)

        if scene == "bus":
            draw.rounded_rectangle((130, 305, 865, 500), radius=34, fill=dark)
            for x in range(195, 760, 115):
                draw.rectangle((x, 335, x + 80, 390), fill="#d9ecff")
            draw.rectangle((690, 390, 805, 500), fill="#31475d")
            for wheel_x in (230, 690):
                draw.ellipse((wheel_x, 465, wheel_x + 115, 580), fill="#111820")
                draw.ellipse((wheel_x + 34, 499, wheel_x + 81, 546), fill="#edf4fb")
            for x in (920, 1065):
                draw_generated_charger(draw, x, 195, 0.9, accent)
            draw.line((990, 430, 855, 455), fill=accent, width=12)
        elif scene == "highway":
            draw.polygon([(0, 720), (530, 380), (750, 380), (1280, 720)], fill="#cbd3de")
            draw.line((640, 390, 640, 720), fill="#ffffff", width=8)
            draw_generated_car(draw, 235, 405, 0.95, dark)
            draw_generated_charger(draw, 930, 205, 1.0, accent)
            draw.line((960, 470, 760, 500), fill=accent, width=13)
        elif scene == "parking":
            for x in range(110, 1180, 180):
                draw.line((x, 575, x + 75, 680), fill="#ffffff", width=5)
            draw.rectangle((100, 170, 410, 560), fill="#dce3ec")
            for y in range(220, 500, 70):
                draw.rectangle((145, y, 365, y + 38), fill="#f8fbff")
            draw_generated_car(draw, 460, 410, 0.9, dark)
            draw_generated_charger(draw, 950, 230, 0.95, accent)
        elif scene == "battery":
            draw_generated_car(draw, 145, 420, 0.9, dark)
            for x, height in [(740, 250), (880, 320), (1020, 210)]:
                draw.rounded_rectangle((x, 510 - height, x + 95, 510), radius=16, fill="#ffffff", outline=accent, width=6)
                draw.rectangle((x + 25, 510 - height - 24, x + 70, 510 - height), fill=accent)
                draw.rectangle((x + 20, 480 - int(height * 0.55), x + 75, 488), fill=accent)
            draw.arc((705, 235, 1155, 610), 200, 20, fill=accent, width=10)
        elif scene == "hub":
            for x in (730, 880, 1030):
                draw_generated_charger(draw, x, 230 + rng.randint(-15, 15), 0.85, accent)
            draw.rounded_rectangle((120, 310, 620, 505), radius=18, fill="#ffffff", outline=dark, width=5)
            draw.rectangle((155, 350, 585, 390), fill="#d9ecff")
            draw.line((155, 430, 585, 430), fill=ground, width=10)
            draw_generated_car(draw, 315, 500, 0.55, dark)
        elif scene == "fast":
            draw_generated_car(draw, 135, 385, 1.0, dark)
            draw_generated_charger(draw, 870, 175, 1.12, accent)
            for offset in (0, 75, 150):
                draw.arc((1000 + offset, 210 + offset // 3, 1190 + offset, 475 + offset // 3), 245, 80, fill=accent, width=10)
            draw.line((920, 505, 760, 500), fill=accent, width=16)
        else:
            draw_generated_car(draw, 170, 360, 1.0, dark)
            draw_generated_charger(draw, 880, 180, 1.0, accent)
            draw.arc((1010, 320, 1160, 500), 250, 90, fill=accent, width=12)

        for _ in range(18):
            x = rng.randint(70, 1210)
            y = rng.randint(70, 210)
            radius = rng.randint(2, 5)
            draw.ellipse((x, y, x + radius, y + radius), fill=ground)
    except Exception:
        pass

    path = MEDIA_DIR / f"{aid}_generated_{scene}_{int(time.time())}.png"
    image.save(path)
    return [{"path": str(path.relative_to(BASE_DIR)), "caption": ""}]


def is_china_candidate(candidate: Candidate) -> bool:
    haystack = f"{candidate.source} {candidate.region} {candidate.title} {candidate.summary} {candidate.link}".lower()
    if "china" in haystack or "cnevpost" in haystack or "d1ev" in haystack or "36kr" in haystack:
        return True
    return bool(re.search(r"[\u3400-\u9fff]", haystack))


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


def provider_setting(name: str, qa: bool = False) -> tuple[str, str, str | None, str] | None:
    if name == "openrouter" and OPENROUTER_API_KEY:
        return "openrouter", OPENROUTER_MODEL, OPENROUTER_BASE_URL, OPENROUTER_API_KEY
    if name == "gemini" and GEMINI_API_KEY:
        return "gemini", GEMINI_MODEL, GEMINI_BASE_URL, GEMINI_API_KEY
    if name == "groq" and GROQ_API_KEY:
        return "groq", GROQ_MODEL, GROQ_BASE_URL, GROQ_API_KEY
    if name == "openai" and OPENAI_API_KEY:
        model = TRANSLATION_QA_MODEL if qa else OPENAI_MODEL
        return "openai", model, OPENAI_BASE_URL, OPENAI_API_KEY
    if name == "kimi" and KIMI_API_KEY:
        return "kimi", KIMI_MODEL, KIMI_BASE_URL, KIMI_API_KEY
    return None


def has_any_llm_provider() -> bool:
    return any((OPENROUTER_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, KIMI_API_KEY))


def recent_rejection_feedback(limit: int = 5) -> list[str]:
    if not HISTORY_DB:
        return []
    try:
        return HISTORY_DB.get_recent_rejection_feedback(limit=limit)
    except Exception as exc:
        print(f"[history] Could not load recent rejection feedback: {exc}")
        return []


def build_dynamic_system_prompt(base_prompt: str) -> str:
    """Add the latest owner rejection reasons to the LLM system prompt."""
    mistakes = recent_rejection_feedback(limit=5)
    if not mistakes:
        return base_prompt

    lines = [
        base_prompt.rstrip(),
        "",
        "CRITICAL SHORT-TERM OWNER FEEDBACK:",
        "The owner recently rejected drafts for these reasons. Avoid repeating these exact mistakes in the current draft:",
    ]
    for index, mistake in enumerate(mistakes, start=1):
        lines.append(f"{index}. {mistake}")
    lines.extend(
        [
            "",
            "Before returning JSON, check the draft against these rejection patterns.",
            "If a similar pattern appears, rewrite that fragment.",
            "Keep following channel_agent_rules.md and return strict JSON only.",
        ]
    )
    return "\n".join(lines)


def default_provider_chain(candidate: Candidate) -> list[str]:
    provider_pref = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if provider_pref:
        return split_provider_chain(provider_pref)
    if is_china_candidate(candidate):
        return ["kimi", "openrouter", "gemini", "groq", "openai"]
    return ["openai", "openrouter", "gemini", "groq", "kimi"]


def llm_settings_chain(candidate: Candidate) -> list[tuple[str, str, str | None, str]]:
    names = split_provider_chain(LLM_PROVIDER_CHAIN) if LLM_PROVIDER_CHAIN else default_provider_chain(candidate)
    settings: list[tuple[str, str, str | None, str]] = []
    for name in dedupe_provider_names(names):
        if name in {"template", "none", "off"}:
            continue
        setting = provider_setting(name, qa=False)
        if setting:
            settings.append(setting)
    return settings


def llm_settings(candidate: Candidate) -> tuple[str, str, str | None, str]:
    settings = llm_settings_chain(candidate)
    if settings:
        return settings[0]
    raise ValueError("LLM API key is missing")


def llm_client(api_key: str, base_url: str | None) -> OpenAI:
    kwargs = {"api_key": api_key, "timeout": 60.0}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def usage_value(usage, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(name) or 0)
    return int(getattr(usage, name, 0) or 0)


def log_llm_usage(provider: str, model: str, phase: str, candidate: Candidate | None, response) -> None:
    usage = getattr(response, "usage", None)
    total = usage_value(usage, "total_tokens")
    prompt = usage_value(usage, "prompt_tokens")
    completion = usage_value(usage, "completion_tokens")
    if not any((total, prompt, completion)):
        return

    title = clean_text(candidate.title if candidate else "")
    title = title.replace("|", "/")[:120]
    is_new = not LLM_USAGE_LOG_PATH.exists()
    with LLM_USAGE_LOG_PATH.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write("# llm_usage\n\n")
            handle.write("| time | provider | model | phase | input | output | total | title |\n")
            handle.write("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
        handle.write(
            f"| {datetime.now(timezone.utc).isoformat()} | {provider} | {model} | {phase} | {prompt} | {completion} | {total} | {title} |\n"
        )


def facts_plain_text(facts: dict) -> str:
    parts = [
        str(facts.get("headline") or ""),
        str(facts.get("lead") or ""),
        str(facts.get("takeaway") or ""),
    ]
    parts.extend(str(item) for item in facts.get("details", []) if item)
    return " ".join(parts)


def apply_translation_glossary(value):
    replacements = [
        (r"\bИдеал\s*MEGA\b", "Li Auto MEGA"),
        (r"\bИдеалMEGA\b", "Li Auto MEGA"),
        (r"\bИдеал\s+авто\b", "Li Auto"),
        (r"\bИдеал\s+Авто\b", "Li Auto"),
        (r"\bИдеала\b", "Li Auto"),
        (r"\bИдеалем\b", "Li Auto"),
        (r"\bИдеале\b", "Li Auto"),
        (r"\bИдеал\b", "Li Auto"),
        (r"\bЛисян\b", "Li Auto"),
        (r"\bЛи\s*Сян\b", "Li Auto"),
        (r"\bТесла\b", "Tesla"),
        (r"\bБИД\b", "BYD"),
        (r"\bБиЯДи\b", "BYD"),
        (r"\bНио\b", "NIO"),
        (r"\bСяопэн\b", "XPeng"),
        (r"\bКсиаопэн\b", "XPeng"),
        (r"\bзарядные\s+столбы\b", "зарядные станции"),
        (r"\bзарядных\s+столбов\b", "зарядных станций"),
        (r"\bзарядными\s+столбами\b", "зарядными станциями"),
        (r"\bстолбы\s+зарядки\b", "зарядные станции"),
        (r"\bстолб\s+зарядки\b", "зарядная станция"),
        (r"\bзарядный\s+столб\b", "зарядная станция"),
        (r"\bзарядного\s+столба\b", "зарядной станции"),
        (r"\bобычного\s+розетки\b", "обычной розетки"),
        (r"\bНочное\s+зарядка\b", "Ночная зарядка"),
        (r"\bночное\s+зарядка\b", "ночная зарядка"),
        (r"\bЕжедневный\s+поездка\b", "Ежедневная поездка"),
        (r"\bautonomy\b", "запаса хода"),
        (r"\bdaily driving\b", "ежедневный пробег"),
        (r"сеть зарядных станций\s+Chocolate", "сеть станций замены батарей Chocolate"),
        (r"зарядных станций\s+Chocolate", "станций замены батарей Chocolate"),
        (r"зарядные станции\s+Chocolate", "станции замены батарей Chocolate"),
    ]

    if isinstance(value, str):
        result = value
        for pattern, replacement in replacements:
            result = re.sub(pattern, replacement, result, flags=re.I)
        return clean_text(result)
    if isinstance(value, list):
        return [apply_translation_glossary(item) for item in value]
    if isinstance(value, dict):
        return {key: apply_translation_glossary(item) for key, item in value.items()}
    return value


def translation_qa_settings(candidate: Candidate) -> tuple[str, str, str | None, str] | None:
    settings = translation_qa_settings_chain(candidate)
    return settings[0] if settings else None


def translation_qa_settings_chain(candidate: Candidate) -> list[tuple[str, str, str | None, str]]:
    raw = TRANSLATION_QA_PROVIDER_CHAIN or os.environ.get("TRANSLATION_QA_PROVIDER") or LLM_PROVIDER_CHAIN
    names = split_provider_chain(raw) if raw else default_provider_chain(candidate)
    settings: list[tuple[str, str, str | None, str]] = []
    for name in dedupe_provider_names(names):
        if name in {"template", "none", "off"}:
            continue
        setting = provider_setting(name, qa=True)
        if setting:
            settings.append(setting)
    return settings


def semantic_review_facts(candidate: Candidate, article_text: str, facts: dict) -> dict:
    if not TRANSLATION_QA_ENABLED:
        return facts
    settings_chain = translation_qa_settings_chain(candidate)
    if not settings_chain:
        return facts

    initial_json = json.dumps(facts, ensure_ascii=False, indent=2)
    prompt = f"""
You are a bilingual semantic editor for a Russian Telegram channel about EV charging.
Check the Russian draft JSON against the original source text. Fix mistranslations, literal brand translations, grammar, and mixed-language fragments.
Return ONLY valid JSON, no markdown.

Output shape:
{{
  "headline": "Russian headline",
  "lead": "1-2 Russian sentences",
  "details": ["3-5 short Russian facts, each under 120 characters"],
  "takeaway": "",
  "qa_score": 1,
  "qa_notes": "short Russian note about what was fixed"
}}

Mandatory glossary and sense rules:
- 理想 / 理想汽车 = Li Auto, never "Идеал".
- 理想MEGA = Li Auto MEGA.
- 特斯拉 = Tesla.
- 比亚迪 = BYD.
- 蔚来 = NIO.
- 小鹏 = XPeng.
- 宁德时代 = CATL.
- 小米汽车 = Xiaomi Auto.
- 问界 = AITO.
- 特来电 = Teld.
- 星星充电 = Star Charge.
- 国家电网 = State Grid.
- 东风 = Dongfeng.
- 欣旺达 = Sunwoda.
- 充电桩 = зарядная станция / домашняя зарядная станция / зарядное устройство, never "зарядный столб".
- 超充 = сверхбыстрая зарядка.
- 快充 = быстрая зарядка.
- 电芯 = аккумуляторная ячейка, not a charging station.
- 干法正极 = сухой положительный электрод.
- Keep company and model names clear; if unsure, keep Latin brand spelling.
- Remove English fragments like "daily driving", "autonomy", "reduced the cost" from Russian output.
- Do not add a final conclusion about Russia/RF unless the article is directly about Russia.
- Do not invent facts. If a fact is not supported, remove or soften it.
- Keep enough substance for a Telegram post: headline + lead + 4 useful facts should produce about 700-1100 Russian characters after rendering.
- If the initial draft is too short, expand using supported facts from the source: numbers, models, technologies, charging time, temperature, power, battery type, usage scenario.
- If the initial draft is semantically unreliable, fix it; if it cannot be fixed from the source, set qa_score to 1 or 2.
- qa_score: 5 = clean, 4 = minor fixes, 3 = acceptable after fixes, 1-2 = do not publish.

Source: {candidate.source}
Region: {candidate.region}
Original title: {candidate.title}
Original summary: {candidate.summary}
Original article text:
{article_text[:5000]}

Initial Russian JSON:
{initial_json}
"""
    base_request = {
        "messages": [
            {"role": "system", "content": "You correct semantic translation errors and return strict JSON."},
            {"role": "user", "content": prompt},
        ],
    }
    reviewed = None
    qa_provider = ""
    qa_model = ""
    last_error = None
    for qa_provider, qa_model, qa_base_url, qa_api_key in settings_chain:
        request = dict(base_request)
        request["model"] = qa_model
        if qa_model.lower().startswith("gpt-5"):
            request["max_completion_tokens"] = 1000
        else:
            request["temperature"] = 0.1
            request["max_tokens"] = 900
        try:
            response = llm_client(qa_api_key, qa_base_url).chat.completions.create(**request)
            log_llm_usage(qa_provider, qa_model, "translation_qa", candidate, response)
            content = response.choices[0].message.content.strip()
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.I | re.M).strip()
            reviewed = json.loads(content)
            break
        except Exception as exc:
            last_error = exc
            print(f"Translation QA provider failed: {qa_provider}/{qa_model} :: {exc}")
            continue

    if reviewed is None:
        if is_china_candidate(candidate) or has_cjk_text(candidate.title + article_text):
            raise ValueError(f"Translation semantic QA failed to run: {last_error}")
        return facts

    reviewed = apply_translation_glossary(strip_cjk(reviewed))
    score = int(reviewed.get("qa_score") or reviewed.get("_translation_qa_score") or 0)
    if score and score < TRANSLATION_QA_MIN_SCORE:
        raise ValueError(f"Translation semantic QA rejected draft: score {score}, {reviewed.get('qa_notes', '')}")

    facts.update(
        {
            "headline": reviewed.get("headline") or facts.get("headline"),
            "lead": reviewed.get("lead") or facts.get("lead"),
            "details": reviewed.get("details") or facts.get("details"),
            "takeaway": "",
            "_translation_qa_provider": qa_provider,
            "_translation_qa_model": qa_model,
            "_translation_qa_score": score or "",
            "_translation_qa_notes": reviewed.get("qa_notes") or "",
        }
    )
    return facts


def shorten_fact(value: str, limit: int = 118) -> str:
    value = clean_text(strip_urls(strip_emoji(value)))
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip(" ,.;:-") + "…"


def has_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", value or ""))


def extract_number_mentions(text: str, limit: int = 6) -> list[str]:
    pattern = r"(?:\d+[\d\s.,]*\s?(?:кВт|МВт|ГВт|км|порт\w*|станц\w*|заряд\w*|%|руб\.?|млн|млрд|тыс\.?|h|kW|MW|GW|km|ports?|chargers?|stations?))"
    mentions = []
    for match in re.findall(pattern, text or "", flags=re.I):
        item = clean_text(match)
        if item and item not in mentions:
            mentions.append(item)
        if len(mentions) >= limit:
            break
    return mentions


def template_facts(candidate: Candidate, article_text: str, reason: str = "") -> dict:
    raw_title = clean_text(candidate.title)
    headline = raw_title if has_cyrillic(raw_title) else "Материал требует ручной редакции"
    source_region = ", ".join(part for part in [candidate.source, candidate.region] if clean_text(part))
    summary = clean_text(candidate.summary)
    text_basis = " ".join(part for part in [summary, article_text] if part)
    first_sentence = clean_text(re.split(r"(?<=[.!?])\s+", text_basis)[0] if text_basis else "")
    if has_cyrillic(first_sentence) and len(first_sentence) > 40:
        lead = shorten_fact(first_sentence, 220)
    else:
        lead = "Автоматический автор недоступен, поэтому этот материал нельзя считать готовым постом для канала."

    numbers = extract_number_mentions(" ".join([raw_title, summary, article_text]))
    details = [
        shorten_fact(raw_title, 118),
        shorten_fact(source_region or "Источник не определен", 118),
        shorten_fact(f"Найденные цифры: {', '.join(numbers)}" if numbers else "Нет надежно извлеченных цифр по мощности, портам или срокам", 118),
        "Нужна ручная редактура: проверить факты, зарядный сценарий, медиа и практический смысл",
    ]
    return {
        "headline": headline,
        "lead": lead,
        "details": [item for item in details if item],
        "takeaway": "",
        "_llm_provider": "template",
        "_llm_model": "rule-based",
        "_llm_error": shorten_fact(reason, 240),
    }


def extract_facts(candidate: Candidate, article_text: str) -> dict:
    settings_chain = llm_settings_chain(candidate)
    if not settings_chain:
        if LLM_TEMPLATE_FALLBACK and ALLOW_RULE_BASED_DRAFTS:
            return template_facts(candidate, article_text, "No configured LLM providers")
        raise ValueError("No configured LLM providers for channel-ready draft")

    editorial_memory = load_editorial_memory()
    china_note = """
China-source mode:
- Treat Chinese-source stories as a separate discovery lane.
- Prefer concrete China facts: charging networks, BYD/NIO/State Grid/Teld/Star Charge, ultra-fast charging, battery swap, chargers on highways, remote charging, connectors, prices, deployment scale.
- Do not write about China just because it is China; keep only stories useful to Russian EV/charging readers.
- Translate Chinese company/product/location names carefully and keep original brand names where needed.
""" if is_china_candidate(candidate) else ""
    prompt = f"""
Extract facts for a Russian Telegram post about EV charging.
Return ONLY valid JSON, no markdown.

Use these editorial rules as binding style memory:
{editorial_memory}
{china_note}

JSON shape:
{{
  "headline": "Russian headline with a number or clear meaning",
  "lead": "1-2 Russian sentences explaining the news",
  "details": ["4 short useful Russian facts, each under 120 characters"],
  "takeaway": ""
}}

Hard rules:
- Russian only, except company names, model names, locations, and URLs.
- No Chinese characters in the final JSON values.
- No press release tone.
- No politics, Biden, geopolitics, import substitution, foreign dependency.
- No vague phrases like infrastructure improvement, new opportunities, convenient and available.
- Do not include emoji in JSON fields. The renderer adds emoji itself.
- Do not include URLs in JSON fields; the renderer adds the source link.
- Do not invent facts.
- Translate place names correctly: Idaho = Айдахо, Penang = Пенанг, Boston = Бостон / в Бостоне.
- "more than a dozen" = "еще 12+" or "более 12", not "более двенадцати".
- "East Coast states" = "штаты Восточного побережья", not "построить восточных штатах".
- In EV charging context, "commissioning" = "ввод в эксплуатацию", not "комиссия".
- "e-mobility services" = "сервисы для зарядных сетей" or "сервисы для EV", not "электромобилейные услуги".
- In China battery-swap context, "换电" / "换电站" / CATL Chocolate = "замена батарей" / "станция замены батарей", not "зарядная станция".
- If the article is about apartments, parking, offices, or retail, explain the practical value for residents, management companies, developers, parking owners, or site owners.
- Do not use formulaic wording like "как пример для РФ" or "Для РФ это пример".
- Always look at the story from the position of a Russian channel reader: is it useful for buying, locating, operating, securing, subsidizing, or understanding EV charging demand?
- Do not add a separate final conclusion about Russia, RF, Russian realities, or Russian market unless the article itself is directly about Russia.
- Keep practical meaning inside the lead or fact bullets only when it naturally belongs there.
- Strong topics: Russian subsidies and regulation, charging hubs, containerized/mobile hubs, remote routes, ultra-fast charging, reliability, theft/security, connectors for cars coming to Russia, battery-market shifts that affect EV demand.
- Softly remind that charging projects are assembled for the site: AC/DC, power, payment, service, monitoring. This should sound like expert help, not direct advertising.

Article source: {candidate.source}
Region: {candidate.region}
Title: {candidate.title}
Summary: {candidate.summary}
Article text: {article_text}
Link: {candidate.link}
"""
    system_prompt = build_dynamic_system_prompt(
        "You extract facts for a practical Russian EV charging Telegram channel. "
        "Return strict JSON only. Avoid recent owner rejection patterns."
    )
    base_request = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    errors = []
    for provider, model, base_url, api_key in settings_chain:
        request = dict(base_request)
        request["model"] = model
        if model.lower().startswith("gpt-5"):
            request["max_completion_tokens"] = 900
        else:
            request["temperature"] = 0.65 if provider == "kimi" else 0.35
            request["max_tokens"] = 700
        try:
            response = llm_client(api_key, base_url).chat.completions.create(**request)
            log_llm_usage(provider, model, "extract_facts", candidate, response)
            content = response.choices[0].message.content.strip()
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.I | re.M).strip()
            facts = json.loads(content)
            facts = apply_translation_glossary(strip_cjk(facts))
            facts = semantic_review_facts(candidate, article_text, facts)
            facts = apply_translation_glossary(strip_cjk(facts))
            facts["_llm_provider"] = provider
            facts["_llm_model"] = model
            return facts
        except Exception as exc:
            errors.append(f"{provider}/{model}: {exc}")
            print(f"LLM provider failed: {provider}/{model} :: {exc}")
            continue

    if LLM_TEMPLATE_FALLBACK and ALLOW_RULE_BASED_DRAFTS:
        return template_facts(candidate, article_text, "; ".join(errors[-3:]))
    raise ValueError("LLM provider chain failed: " + "; ".join(errors[-3:]))

def render_post(candidate: Candidate, facts: dict) -> str:
    headline = strip_urls(strip_emoji(clean_text(str(facts.get("headline") or candidate.title))))
    lead = strip_urls(strip_emoji(clean_text(str(facts.get("lead") or candidate.summary))))
    details = [strip_urls(strip_emoji(clean_text(str(item)))) for item in facts.get("details", []) if clean_text(str(item))]

    if len(details) < 3:
        raise ValueError("Not enough useful details")

    emoji = ["🔋", "🚗", "🏢", "📌", "⚡"]
    lines = [f"<b>{html.escape(headline)}</b>", "", html.escape(lead), ""]
    for index, detail in enumerate(details[:5]):
        lines.append(f"{emoji[index % len(emoji)]} {html.escape(detail)}")
    source_url = html.escape(candidate.link, quote=True)
    lines.extend(["", f'Источник: <a href="{source_url}">открыть оригинал</a>'])
    post = "\n".join(lines)
    post = polish_russian(post)
    validate_post_quality(post)
    return post


def polish_russian(post: str) -> str:
    replacements = {
        "\u0431\u043e\u043b\u0435\u0435 \u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u0438": "12+",
        "\u0435\u0449\u0435 \u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c": "\u0435\u0449\u0435 12",
        "\u0435\u0449\u0435 \u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u0438": "\u0435\u0449\u0435 12",
        "\u043f\u043e\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u0432\u043e\u0441\u0442\u043e\u0447\u043d\u044b\u0445 \u0448\u0442\u0430\u0442\u0430\u0445": "\u043f\u043e\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u0432 \u0448\u0442\u0430\u0442\u0430\u0445 \u0412\u043e\u0441\u0442\u043e\u0447\u043d\u043e\u0433\u043e \u043f\u043e\u0431\u0435\u0440\u0435\u0436\u044c\u044f",
        "\u043d\u0430 \u0412\u043e\u0441\u0442\u043e\u043a\u0435 \u0421\u0428\u0410": "\u0432 \u0448\u0442\u0430\u0442\u0430\u0445 \u0412\u043e\u0441\u0442\u043e\u0447\u043d\u043e\u0433\u043e \u043f\u043e\u0431\u0435\u0440\u0435\u0436\u044c\u044f \u0421\u0428\u0410",
        "\u043d\u0430 \u0432\u043e\u0441\u0442\u043e\u043a\u0435 \u0421\u0428\u0410": "\u0432 \u0448\u0442\u0430\u0442\u0430\u0445 \u0412\u043e\u0441\u0442\u043e\u0447\u043d\u043e\u0433\u043e \u043f\u043e\u0431\u0435\u0440\u0435\u0436\u044c\u044f \u0421\u0428\u0410",
        "\u0432 \u0411\u043e\u0441\u0442\u043e\u043d ": "\u0432 \u0411\u043e\u0441\u0442\u043e\u043d\u0435 ",
        "\u0432 \u0411\u043e\u0441\u0442\u043e\u043d:": "\u0432 \u0411\u043e\u0441\u0442\u043e\u043d\u0435:",
        "\u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044e,": "\u0432\u0432\u043e\u0434 \u0432 \u044d\u043a\u0441\u043f\u043b\u0443\u0430\u0442\u0430\u0446\u0438\u044e,",
        "\u043a\u043e\u043c\u0438\u0441\u0441\u0438\u044e": "\u0432\u0432\u043e\u0434 \u0432 \u044d\u043a\u0441\u043f\u043b\u0443\u0430\u0442\u0430\u0446\u0438\u044e",
        "\u044d\u043b\u0435\u043a\u0442\u0440\u043e\u043c\u043e\u0431\u0438\u043b\u0435\u0439\u043d\u044b\u043c\u0438 \u0443\u0441\u043b\u0443\u0433\u0430\u043c\u0438": "\u0441\u0435\u0440\u0432\u0438\u0441\u0430\u043c\u0438 \u0434\u043b\u044f EV",
        "\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435": "\u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u043e\u0435 \u0432\u044b\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
        "\u044d\u0442\u043e \u0432\u0430\u0436\u0435\u043d \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440": "\u044d\u0442\u043e \u0432\u0430\u0436\u043d\u044b\u0439 \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440",
    }
    for old, new in replacements.items():
        post = post.replace(old, new)
    return post


def strip_emoji(value: str) -> str:
    return re.sub(r"\s+", " ", EMOJI_RE.sub("", value)).strip()


def strip_urls(value: str) -> str:
    value = re.sub(r"https?://\\S+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_cjk(value):
    if isinstance(value, str):
        return re.sub(r"\s+", " ", re.sub(r"[\u3000-\u30ff\u3400-\u9fff]+", "", value)).strip()
    if isinstance(value, list):
        return [strip_cjk(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_cjk(item) for key, item in value.items()}
    return value


def validate_post_quality(post: str) -> None:
    lowered = post.lower()
    for marker in BAD_TEXT_MARKERS:
        if marker in lowered:
            raise ValueError(f"Generated post failed quality filter: {marker}")
    semantic_bad_markers = [
        "идеал авто",
        "идеалmega",
        "зарядные столбы",
        "столбы зарядки",
        "зарядный столб",
        "daily driving",
        "autonomy",
        "reduced the cost",
        "granularly",
        "от обычного розетки",
        "ночное зарядка",
        "ежедневный поездка",
    ]
    for marker in semantic_bad_markers:
        if marker in lowered:
            raise ValueError(f"Generated post failed semantic translation filter: {marker}")
    if re.search(r"[\u3400-\u9fff]", post):
        raise ValueError("Generated post contains CJK characters")
    text_only = re.sub(r"<[^>]+>", "", post).strip()
    if len(text_only) < MIN_POST_CHARS:
        raise ValueError("Generated post is too short")
    if len(text_only) > 1600:
        raise ValueError("Generated post is too long")


TEMPORARY_BUILD_ERRORS = [
    "connection error",
    "no usable media",
    "contains cjk",
    "generated post is too short",
    "not enough useful details",
    "translation semantic qa",
]


def should_mark_build_error_rejected(error_text: str) -> bool:
    return not any(marker in error_text for marker in TEMPORARY_BUILD_ERRORS)


def save_review_mapping(draft_path: Path, messages) -> None:
    try:
        mapping = {}
        if REVIEW_MAP_PATH.exists():
            mapping = json.loads(REVIEW_MAP_PATH.read_text(encoding="utf-8"))
        for message in messages:
            mapping[str(message.message_id)] = draft_path.name
        REVIEW_MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Could not save review mapping: {exc}")


def draft_action_keyboard(draft_id: str | Path) -> InlineKeyboardMarkup:
    return build_draft_action_keyboard(draft_id)




def load_china_test_state() -> dict:
    if not CHINA_TEST_STATE_PATH.exists():
        return {}
    try:
        return json.loads(CHINA_TEST_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_china_test_state(state: dict) -> None:
    CHINA_TEST_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_china_markdown(path: Path, title: str, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or not path.read_text(encoding="utf-8", errors="ignore").strip()
    with path.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write(f"# {path.stem}\n\n")
        handle.write(f"## {title}\n\n")
        handle.write("| " + " | ".join(headers) + " |\n")
        handle.write("| " + " | ".join(["---"] * len(headers)) + " |\n")
        for row in rows:
            safe_row = [str(cell).replace("\n", " ").replace("|", "/") for cell in row]
            handle.write("| " + " | ".join(safe_row) + " |\n")
        handle.write("\n")


def group_candidates_by_source(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    grouped: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.source or "Unknown", []).append(candidate)
    return grouped


def probe_china_media(candidate: Candidate) -> bool:
    try:
        article_html = fetch_article_html(candidate.link)
        image_urls = targeted_image_search(candidate, article_html, candidate.title, limit=5)
        if not image_urls:
            image_urls = deep_image_candidates(candidate, article_html, aggressive=False)
        media = download_images(image_urls, f"china_test_{article_id(candidate.link)}_{int(time.time())}", limit=1)
        return bool(media)
    except Exception as exc:
        china_record_error(candidate.source, f"media probe failed: {exc}")
        return False


def translation_quality_score(candidate: Candidate) -> int:
    try:
        article_html = fetch_article_html(candidate.link)
        article_text = extract_article_text(article_html)
        facts = extract_facts(candidate, article_text)
        score = 2
        if facts.get("headline"):
            score += 1
        if facts.get("lead"):
            score += 1
        details = facts.get("details") or []
        if isinstance(details, list) and len(details) >= 3:
            score += 1
        if has_cjk_text(json.dumps(facts, ensure_ascii=False)):
            score -= 1
        return max(1, min(5, score))
    except Exception as exc:
        china_record_error(candidate.source, f"translation quality failed: {exc}")
        return 1


def china_stats_rows(candidates: list[Candidate], probe_media: bool = True, probe_translation: bool = True) -> list[dict]:
    grouped = group_candidates_by_source(candidates)
    rows: list[dict] = []
    probe_limit = int(os.environ.get("CHINA_MEDIA_PROBE_LIMIT", "4"))
    translation_limit = int(os.environ.get("CHINA_TRANSLATION_PROBE_LIMIT", "1"))
    all_sources = sorted(set(grouped) | set(CHINA_STATS))

    for source in all_sources:
        items = grouped.get(source, [])
        media_success = 0
        if probe_media:
            for candidate in items[:probe_limit]:
                ok = probe_china_media(candidate)
                china_record_media(source, ok)
                media_success += 1 if ok else 0

        quality_scores: list[int] = []
        if probe_translation:
            for candidate in items[:translation_limit]:
                score = translation_quality_score(candidate)
                quality_scores.append(score)
                china_record_translation(source, score)

        stats = china_source_stats(source)
        stats["found"] = len(items)
        translation_scores = stats.get("translation_scores") or quality_scores
        quality = round(sum(translation_scores) / len(translation_scores), 1) if translation_scores else 0
        rows.append(
            {
                "source": source,
                "found": len(items),
                "media_success": stats.get("media_success", media_success),
                "media_checked": stats.get("media_checked", min(len(items), probe_limit)),
                "errors": list(stats.get("errors") or []),
                "translation_quality": quality,
                "article_ids": [article_id(candidate.link) for candidate in items],
            }
        )
    return rows


def write_china_stats_logs(rows: list[dict], run_time: datetime) -> None:
    title = run_time.strftime("%Y-%m-%d %H:%M UTC")
    append_china_markdown(
        CHINA_TEST_RUN_PATH,
        title,
        ["Время", "Источник", "Найдено статей", "Успешно скачано медиа", "Ошибки"],
        [
            [
                title,
                row["source"],
                row["found"],
                f'{row["media_success"]}/{row["media_checked"]}',
                china_error_summary(row["errors"]),
            ]
            for row in rows
        ],
    )
    append_china_markdown(
        CHINA_DAILY_STATS_PATH,
        title,
        ["Дата/время", "Источник", "Найдено статей", "Успешно скачано медиа", "Ошибки", "Качество перевода"],
        [
            [
                title,
                row["source"],
                row["found"],
                f'{row["media_success"]}/{row["media_checked"]}',
                china_error_summary(row["errors"]),
                row["translation_quality"] or "-",
            ]
            for row in rows
        ],
    )


def update_china_test_state(rows: list[dict], run_time: datetime, duration_hours: int = 0) -> dict:
    state = load_china_test_state()
    if not state.get("started_at"):
        state["started_at"] = run_time.isoformat()
    state.setdefault("runs", [])
    state["runs"].append({"time": run_time.isoformat(), "rows": rows})
    state["last_run_at"] = run_time.isoformat()
    if duration_hours:
        started_at = datetime.fromisoformat(state["started_at"])
        state["deadline_at"] = (started_at + timedelta(hours=duration_hours)).isoformat()
    save_china_test_state(state)
    return state


def china_report_aggregate(state: dict, since_hours: int | None = None) -> dict:
    now = utc_now()
    unique_articles: set[str] = set()
    found_by_source: dict[str, int] = {}
    media_success = 0
    media_checked = 0
    errors: list[str] = []
    for run in state.get("runs") or []:
        try:
            run_time = datetime.fromisoformat(run.get("time"))
        except Exception:
            run_time = now
        if since_hours is not None and run_time < now - timedelta(hours=since_hours):
            continue
        for row in run.get("rows") or []:
            source = row.get("source") or "Unknown"
            found_by_source[source] = found_by_source.get(source, 0) + int(row.get("found") or 0)
            media_success += int(row.get("media_success") or 0)
            media_checked += int(row.get("media_checked") or 0)
            errors.extend(row.get("errors") or [])
            for aid in row.get("article_ids") or []:
                unique_articles.add(aid)
    success_pct = round((media_success / media_checked) * 100, 1) if media_checked else 0
    return {
        "unique_articles": len(unique_articles),
        "found_by_source": found_by_source,
        "media_success": media_success,
        "media_checked": media_checked,
        "success_pct": success_pct,
        "errors": errors,
    }


def china_proxy_recommendation(report: dict) -> str:
    total = report["unique_articles"]
    success_pct = report["success_pct"]
    error_text = china_error_summary(report["errors"]).lower()
    if total >= 8 and success_pct >= 50 and "timeout" not in error_text:
        return "можно продолжать тест на текущем прокси, платный покупать после ещё 1-2 дней стабильности"
    if total < 4 or success_pct < 35 or "502" in error_text or "timeout" in error_text:
        return "лучше готовиться к платному proxy ближе к Китаю/HK/SG или residential"
    return "прокси рабочий, но выборка средняя: тестировать ещё сутки перед покупкой"


def format_china_report(report: dict, final: bool = False) -> str:
    title = "Итоговый отчёт по китайскому парсингу" if final else "Вечерний отчёт по китайскому парсингу"
    sources = report["found_by_source"]
    source_line = ", ".join(f"{source}: {count}" for source, count in sorted(sources.items())) or "-"
    return (
        f"{title}\n\n"
        f"Всего уникальных статей: {report['unique_articles']}\n"
        f"Медиа: {report['media_success']}/{report['media_checked']} ({report['success_pct']}%)\n"
        f"Источники: {source_line}\n"
        f"Ошибки: {china_error_summary(report['errors'])}\n\n"
        f"Рекомендация: {china_proxy_recommendation(report)}"
    )


async def run_china_test_once(hours: int, send: bool, duration_hours: int = 0) -> None:
    reset_china_stats()
    run_time = utc_now()
    candidates = collect_china_candidates(hours)
    rows = china_stats_rows(candidates)
    write_china_stats_logs(rows, run_time)
    state = update_china_test_state(rows, run_time, duration_hours)
    deadline = state.get("deadline_at")
    if send and deadline:
        try:
            if run_time >= datetime.fromisoformat(deadline) and not state.get("final_sent_at"):
                report = china_report_aggregate(state)
                await notify_review(format_china_report(report, final=True))
                state["final_sent_at"] = run_time.isoformat()
                save_china_test_state(state)
                subprocess.run(["systemctl", "disable", "--now", "headway-china-test.timer"], check=False)
        except Exception as exc:
            print(f"China final report failed: {exc}")
    print(CHINA_TEST_RUN_PATH)


async def send_china_daily_report(send: bool) -> None:
    reset_china_stats()
    run_time = utc_now()
    candidates = collect_china_candidates(24, force=True)
    rows = china_stats_rows(candidates)
    write_china_stats_logs(rows, run_time)
    state = update_china_test_state(rows, run_time)
    state = load_china_test_state()
    text = format_china_report(china_report_aggregate(state, since_hours=24), final=False)
    print(text)
    if send:
        await notify_review(text)


async def send_china_final_report(send: bool) -> None:
    state = load_china_test_state()
    text = format_china_report(china_report_aggregate(state), final=True)
    print(text)
    if send:
        await notify_review(text)


async def notify_review(text: str) -> None:
    if not BOT_TOKEN or not REVIEW_CHAT_ID:
        return
    try:
        bot = Bot(BOT_TOKEN)
        await bot.send_message(chat_id=REVIEW_CHAT_ID, text=text)
    except Exception as exc:
        print(f"Could not send review notification: {exc}")


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


async def notify_monitor_failure(action: str, exc: BaseException) -> None:
    error_name = type(exc).__name__
    error_text = redact_secrets(str(exc))[:700] or "без текста"
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    trace = redact_secrets(trace)[-MONITOR_ERROR_TRACEBACK_CHARS:]
    message = (
        "Сбой проверки новостей Headway.\n"
        f"Режим: {action}\n"
        f"Тип: {error_name}\n"
        f"Ошибка: {error_text}\n\n"
        "Черновик не опубликован автоматически. Подробности сохранены в журнале VPS."
    )
    if trace:
        message += f"\n\nКороткий хвост ошибки:\n{trace}"
    await notify_review(message[:4096])

async def send_review(draft: dict, draft_path: Path | None = None) -> None:
    bot = Bot(BOT_TOKEN)
    sent_messages = []
    intro = await bot.send_message(
        chat_id=REVIEW_CHAT_ID,
        text="Черновик для @headway74. Если ок, ответь на этот черновик: публикуй",
    )
    sent_messages.append(intro)

    media = draft.get("media", [])
    sent_text_as_caption = False
    if media:
        photos = []
        opened = []
        try:
            for index, item in enumerate(media[:10]):
                path = BASE_DIR / item["path"]
                handle = path.open("rb")
                opened.append(handle)
                caption = draft["text"] if index == 0 and len(draft["text"]) <= 1024 else None
                photos.append(InputMediaPhoto(media=handle, caption=caption, parse_mode=ParseMode.HTML if caption else None))
            sent_messages.extend(await bot.send_media_group(chat_id=REVIEW_CHAT_ID, media=photos))
            sent_text_as_caption = len(draft["text"]) <= 1024
        except Exception as exc:
            print(f"Could not send media group for review: {exc}")
            draft["media_status"] = "send_failed"
            draft["needs_media"] = True
            draft["media_error"] = str(exc)
            if draft_path:
                draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
            sent_messages.append(await bot.send_message(
                chat_id=REVIEW_CHAT_ID,
                text=(
                    "Картинки нашлись, но Telegram не принял медиафайл. Черновик отправляю текстом.\n"
                    "Если пост хороший, ответь: ищи или сгенерируй."
                ),
            ))
            media = []
        finally:
            for handle in opened:
                handle.close()

    if not sent_text_as_caption:
        sent_messages.append(await bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=draft["text"],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        ))

    if not media:
        if MEDIA_STATUS_MANAGER:
            missing_media_text = MEDIA_STATUS_MANAGER.format_status_message(MEDIA_STATUS_MANAGER.create_not_found_result())
        else:
            missing_media_text = (
                "Медиа к этой новости не найдено. Если текст хороший, ответь на этот черновик:\n"
                "ищи - углубить поиск изображения\n"
                "сгенерируй - сделать нейтральную иллюстрацию\n"
                "публикуй без фото - отправить пост без медиа\n"
                "отмена - снять черновик"
            )
        sent_messages.append(await bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text=missing_media_text,
        ))

    if draft_path:
        sent_messages.append(await bot.send_message(
            chat_id=REVIEW_CHAT_ID,
            text="Действия с черновиком:",
            reply_markup=draft_action_keyboard(draft_path),
        ))
        save_review_mapping(draft_path, sent_messages)


def build_draft(candidate: Candidate, require_media: bool) -> tuple[dict, str]:
    if not is_valid_source_url(candidate.link):
        raise ValueError("Invalid source URL")

    aid = article_id(candidate.link)
    article_html = fetch_article_html(candidate.link)
    article_published = validate_article_freshness(candidate, article_html)
    article_text = extract_article_text(article_html)
    facts = extract_facts(candidate, article_text)
    post_text = render_post(candidate, facts)
    freshness_base = article_published or candidate.published
    freshness_hours = None
    if freshness_base:
        freshness_hours = round(max(0, (utc_now() - normalize_datetime(freshness_base)).total_seconds() / 3600), 2)

    image_urls = []
    if needs_targeted_equipment_media(candidate):
        image_urls = targeted_image_search(candidate, article_html, candidate.title, limit=10)
    if not image_urls:
        image_urls = deep_image_candidates(candidate, article_html)
    media = download_images(image_urls, aid, limit=5)
    if not media:
        image_urls = deep_image_candidates(candidate, article_html, aggressive=True)
        media = download_images(image_urls, f"{aid}_deep_{int(time.time())}", limit=5)
    if not media:
        image_urls = targeted_image_search(candidate, article_html, candidate.title, limit=10)
        media = download_images(image_urls, f"{aid}_targeted_{int(time.time())}", limit=5)
    if require_media and not media:
        raise ValueError("No usable media for draft")

    draft_version_id = f"{aid}-rewrite-{int(time.time())}"
    draft = {
        "id": draft_version_id,
        "title": candidate.title,
        "headline": candidate.title,
        "text": post_text,
        "media": media,
        "source": candidate.source,
        "region": candidate.region,
        "link": candidate.link,
        "llm_provider": facts.get("_llm_provider", ""),
        "llm_model": facts.get("_llm_model", ""),
        "translation_qa_provider": facts.get("_translation_qa_provider", ""),
        "translation_qa_model": facts.get("_translation_qa_model", ""),
        "translation_qa_score": facts.get("_translation_qa_score", ""),
        "translation_qa_notes": facts.get("_translation_qa_notes", ""),
        "published_at": (article_published or candidate.published).isoformat(),
        "created_at": int(time.time()),
        "media_status": "found_by_search" if media else "not_found",
        "needs_media": not bool(media),
        "media_search_attempts": 1,
        "media_source_info": "article_or_targeted_search" if media else "none",
        "freshness_hours": freshness_hours,
    }
    return draft, aid


def lookback_windows(initial_hours: int) -> list[int]:
    windows: list[int] = []
    for value in [initial_hours, *LOOKBACK_WINDOWS, MAX_LOOKBACK_HOURS]:
        if value <= 0:
            continue
        value = min(value, MAX_LOOKBACK_HOURS)
        if value not in windows:
            windows.append(value)
    return sorted(windows)


def try_build_from_candidates(
    candidates: list[Candidate],
    require_media_passes: list[bool],
    limit: int = 25,
    label: str = "",
) -> tuple[Candidate | None, dict | None, str]:
    for require_media in require_media_passes:
        for candidate in candidates[:limit]:
            if not is_channel_relevant(candidate):
                print(f"Skip candidate: {candidate.title} :: low channel relevance")
                mark_article(candidate, "rejected")
                continue
            try:
                draft, aid = build_draft(candidate, require_media=require_media)
                if label:
                    draft["selection_note"] = label
                return candidate, draft, aid
            except Exception as exc:
                print(f"Skip candidate: {candidate.title} :: {exc}")
                error_text = str(exc).lower()
                if should_mark_build_error_rejected(error_text):
                    mark_article(candidate, "rejected")
    return None, None, ""


async def run_once(hours: int, send: bool) -> None:
    init_db()
    expire_stale_pending_drafts()
    if not has_any_llm_provider() and not ALLOW_RULE_BASED_DRAFTS:
        print("No configured LLM providers; channel-ready drafts are disabled")
        if send:
            await notify_review(
                "Проверка остановлена: на VPS нет настроенного ИИ-ключа для качественного черновика. "
                "Шаблонные посты отключены, чтобы не присылать мусор. Добавь OpenRouter/Gemini/Groq key или включи ручной режим."
            )
        return
    selected = None
    draft = None
    aid = ""
    passes = [True, False] if DAILY_MIN_DRAFT else [True]
    tried_windows: list[int] = []

    for window in lookback_windows(hours):
        tried_windows.append(window)
        candidates = collect_candidates(window)
        print(f"Lookback {window}h candidates: {len(candidates)}")
        if not candidates:
            continue
        selected, draft, aid = try_build_from_candidates(
            candidates,
            passes,
            limit=25,
            label=f"lookback:{window}h",
        )
        if draft and selected:
            break

    if (not draft or not selected) and DAILY_MIN_DRAFT:
        for window in lookback_windows(max(hours, 24)):
            if window in tried_windows and window < 120:
                continue
            relaxed_candidates = collect_relaxed_candidates(window)
            print(f"Relaxed lookback {window}h candidates: {len(relaxed_candidates)}")
            if not relaxed_candidates:
                continue
            selected, draft, aid = try_build_from_candidates(
                relaxed_candidates,
                [False],
                limit=35,
                label=f"relaxed-lookback:{window}h",
            )
            if draft and selected:
                break

    if (not draft or not selected) and DAILY_MIN_DRAFT and CHINA_DIRECT_FALLBACK and not ENABLE_DIRECT_CHINA_SEARCH:
        for window in [24, 48, 72]:
            china_candidates = collect_china_candidates(window, force=True)
            print(f"China direct fallback {window}h candidates: {len(china_candidates)}")
            if not china_candidates:
                continue
            selected, draft, aid = try_build_from_candidates(
                china_candidates,
                [False],
                limit=20,
                label=f"china-direct-fallback:{window}h",
            )
            if draft and selected:
                break

    if not draft or not selected:
        print("No acceptable candidates")
        if send:
            await notify_review(
                f"Проверка прошла: бот расширил поиск назад до {max(lookback_windows(hours))} часов и отдельно проверил Китай, но сильного материала для черновика не нашлось. Ничего не опубликовано."
            )
        return

    draft_path = DRAFT_DIR / f"{int(time.time())}-{aid}.pending.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_draft_history(draft, aid)
    mark_article(selected, "drafted")
    print(draft_path)

    if send:
        await send_review(draft, draft_path)
    return

    candidates = collect_candidates(hours)
    if not candidates:
        print("No candidates")
        if DAILY_MIN_DRAFT and hours < 72:
            candidates = collect_candidates(72)
            print(f"Daily minimum retry candidates: {len(candidates)}")
        if not candidates:
            if send:
                await notify_review('Проверка новостей прошла, но сильного материала для черновика сейчас не нашлось. Бот ничего не публиковал.')
            return

    selected = None
    draft = None
    aid = ""

    passes = [True]
    if DAILY_MIN_DRAFT:
        passes.append(False)

    for require_media in passes:
        for candidate in candidates[:25]:
            try:
                draft, aid = build_draft(candidate, require_media=require_media)
                selected = candidate
                break
            except Exception as exc:
                print(f"Skip candidate: {candidate.title} :: {exc}")
                error_text = str(exc).lower()
                if should_mark_build_error_rejected(error_text):
                    mark_article(candidate, "rejected")
        if draft and selected:
            break

    if not draft or not selected:
        if DAILY_MIN_DRAFT:
            relaxed_candidates = collect_relaxed_candidates(120)
            print(f"Daily minimum relaxed retry candidates: {len(relaxed_candidates)}")
            for candidate in relaxed_candidates[:30]:
                try:
                    draft, aid = build_draft(candidate, require_media=False)
                    selected = candidate
                    break
                except Exception as exc:
                    print(f"Relaxed skip candidate: {candidate.title} :: {exc}")

        if not draft or not selected:
            print("No acceptable candidates")
            if send:
                await notify_review('Проверка новостей прошла, но все найденные материалы отсеялись по качеству, картинкам или релевантности. Бот ничего не публиковал.')
            return

    draft_path = DRAFT_DIR / f"{int(time.time())}-{aid}.pending.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_draft_history(draft, aid)
    mark_article(selected, "drafted")
    print(draft_path)

    if send:
        await send_review(draft, draft_path)


async def rewrite_draft(source_path: str, send: bool) -> None:
    init_db()
    original_path = Path(source_path)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    candidate = Candidate(
        title=original.get("title") or "EV charging draft",
        link=original.get("link") or "",
        summary="",
        source=original.get("source") or "Source",
        region=original.get("region") or "Global",
        published=utc_now(),
        score=1.0,
    )
    if not candidate.link:
        raise ValueError("Cannot rewrite draft without source link")

    aid = article_id(candidate.link)
    article_html = fetch_article_html(candidate.link)
    article_text = extract_article_text(article_html)
    facts = extract_facts(candidate, article_text)
    post_text = render_post(candidate, facts)
    image_urls = image_candidates_from_html(article_html, candidate.link)
    if not image_urls:
        image_urls = raw_image_candidates_from_html(article_html, candidate.link)
    if not image_urls:
        image_urls = fallback_image_search(candidate, aggressive=True)
    media = download_images(image_urls, f"{aid}_rewrite_{int(time.time())}")
    if not media:
        raise ValueError("No usable media for rewritten draft")

    draft_version_id = f"{aid}-mediafix-{int(time.time())}"
    draft = {
        "id": draft_version_id,
        "title": candidate.title,
        "headline": candidate.title,
        "text": post_text,
        "media": media,
        "source": candidate.source,
        "region": candidate.region,
        "link": candidate.link,
        "llm_provider": facts.get("_llm_provider", ""),
        "llm_model": facts.get("_llm_model", ""),
        "created_at": int(time.time()),
        "media_status": "found_by_rewrite_search",
        "needs_media": False,
        "media_search_attempts": int(original.get("media_search_attempts") or 0) + 1,
        "media_source_info": "rewrite_search",
    }
    draft_path = DRAFT_DIR / f"{int(time.time())}-{aid}-rewrite.pending.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_draft_history(draft, draft_version_id)
    print(draft_path)
    if send:
        await send_review(draft, draft_path)


async def revise_media_draft(source_path: str, send: bool, media_hint: str = "") -> None:
    original_path = Path(source_path)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    link = original.get("link") or ""
    if not link:
        raise ValueError("Cannot revise media without source link")

    title = original.get("title") or "EV charging draft"
    candidate = Candidate(
        title=title,
        link=link,
        summary=text_from_html_fragment(original.get("text") or ""),
        source=original.get("source") or "Source",
        region=original.get("region") or "Global",
        published=utc_now(),
        score=1.0,
    )

    aid = article_id(link)
    article_html = fetch_article_html(link)
    used_targeted_search = needs_targeted_equipment_media(candidate, media_hint)
    image_urls = []
    if used_targeted_search:
        image_urls = targeted_image_search(candidate, article_html, media_hint or title, limit=10)
    if not image_urls:
        image_urls = deep_image_candidates(candidate, article_html, aggressive=False)
    if not image_urls:
        image_urls = targeted_image_search(candidate, article_html, media_hint or title, limit=10)
        used_targeted_search = True
    media = download_images(image_urls, f"{aid}_mediafix_{int(time.time())}", limit=5)
    if not media and not used_targeted_search:
        image_urls = targeted_image_search(candidate, article_html, media_hint or title, limit=10)
        media = download_images(image_urls, f"{aid}_mediafix_targeted_{int(time.time())}", limit=5)
    generated_media = False

    draft_version_id = f"{aid}-generated-{int(time.time())}"
    draft = {
        "id": draft_version_id,
        "title": title,
        "headline": title,
        "text": original.get("text") or "",
        "media": media,
        "source": original.get("source") or candidate.source,
        "region": original.get("region") or candidate.region,
        "link": link,
        "created_at": int(time.time()),
        "revision": "media" if media else "media-missing-after-search",
        "media_status": "found_by_media_revision" if media else "not_found",
        "needs_media": not bool(media),
        "media_search_attempts": int(original.get("media_search_attempts") or 0) + 1,
        "media_source_info": "media_revision" if media else "none",
        "generated_media": generated_media,
    }
    draft_path = DRAFT_DIR / f"{int(time.time())}-{aid}-mediafix.pending.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_draft_history(draft, draft_version_id)
    print(draft_path)
    if send:
        await send_review(draft, draft_path)


async def generate_image_draft(source_path: str, send: bool) -> None:
    original_path = Path(source_path)
    original = json.loads(original_path.read_text(encoding="utf-8"))
    link = original.get("link") or ""
    if not link:
        raise ValueError("Cannot generate media without source link")

    aid = article_id(link)
    media = []
    generated_provider = "local"
    if IMAGE_GENERATION_PROVIDER.lower() in {"openai", "gpt", "gpt-image"}:
        media = generate_openai_image(original, aid)
        if media:
            generated_provider = f"openai:{OPENAI_IMAGE_MODEL}"
    if not media:
        media = generate_neutral_image(original, aid)
        generated_provider = "local"
    draft = {
        "id": aid,
        "title": original.get("title") or "EV charging draft",
        "headline": original.get("title") or "EV charging draft",
        "text": original.get("text") or "",
        "media": media,
        "source": original.get("source") or "Source",
        "region": original.get("region") or "Global",
        "link": link,
        "llm_provider": original.get("llm_provider", ""),
        "llm_model": original.get("llm_model", ""),
        "created_at": int(time.time()),
        "revision": "generated-media",
        "media_status": "generated",
        "needs_media": False,
        "media_source_info": generated_provider,
        "generated_media": True,
        "generated_provider": generated_provider,
    }
    draft_path = DRAFT_DIR / f"{int(time.time())}-{aid}-generated.pending.json"
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    record_draft_history(draft, aid)
    print(draft_path)
    if send:
        await send_review(draft, draft_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=8)
    parser.add_argument("--send-review", action="store_true")
    parser.add_argument("--rewrite-draft", default="")
    parser.add_argument("--revise-media-draft", default="")
    parser.add_argument("--generate-image-draft", default="")
    parser.add_argument("--media-hint", default="")
    parser.add_argument("--china-test-run", action="store_true")
    parser.add_argument("--china-daily-report", action="store_true")
    parser.add_argument("--china-final-report", action="store_true")
    parser.add_argument("--test-duration-hours", type=int, default=0)
    args = parser.parse_args()

    if args.china_test_run:
        action = "china-test-run"
        coro = run_china_test_once(args.hours, args.send_review, args.test_duration_hours)
    elif args.china_daily_report:
        action = "china-daily-report"
        coro = send_china_daily_report(args.send_review)
    elif args.china_final_report:
        action = "china-final-report"
        coro = send_china_final_report(args.send_review)
    elif args.generate_image_draft:
        action = "generate-image-draft"
        coro = generate_image_draft(args.generate_image_draft, args.send_review)
    elif args.revise_media_draft:
        action = "revise-media-draft"
        coro = revise_media_draft(args.revise_media_draft, args.send_review, args.media_hint)
    elif args.rewrite_draft:
        action = "rewrite-draft"
        coro = rewrite_draft(args.rewrite_draft, args.send_review)
    else:
        action = "run-once"
        coro = run_once(args.hours, args.send_review)

    try:
        asyncio.run(coro)
    except Exception as exc:
        print(f"Fatal monitor error in {action}: {exc}")
        print(traceback.format_exc())
        if args.send_review:
            try:
                asyncio.run(notify_monitor_failure(action, exc))
            except Exception as notify_exc:
                print(f"Could not notify owner about monitor failure: {notify_exc}")
        raise


if __name__ == "__main__":
    main()
