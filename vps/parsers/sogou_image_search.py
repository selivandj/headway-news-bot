import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parents[1]
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")

CHINA_PROXY_URL = os.environ.get("CHINA_PROXY_URL") or os.environ.get("CHINA_HTTP_PROXY") or ""
CHINA_PROXY_DICT = {"http": CHINA_PROXY_URL, "https": CHINA_PROXY_URL} if CHINA_PROXY_URL else None

SOGOU_IMAGE_URLS = [
    "https://pic.sogou.com/napi/pc/searchList",
    "https://pic.sogou.com/pics/json.jsp",
    "https://pic.sogou.com/pics",
]
DEFAULT_KEYWORDS = ["换电", "超充", "快充", "150kW", "充电站"]
IMAGE_DIR = BASE_DIR / "media" / "images"
LOG_DIR = BASE_DIR / "logs"
STATS_PATH = LOG_DIR / "image_stats.md"

NEGATIVE_MARKERS = [
    "adult",
    "porn",
    "sex",
    "escort",
    "裸",
    "美女",
    "性感",
    "写真",
    "av",
]


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def ensure_dirs() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_image_stats(query: str, status: str, found: int = 0, downloaded: int = 0, error: str = "") -> None:
    ensure_dirs()
    is_new = not STATS_PATH.exists() or not STATS_PATH.read_text(encoding="utf-8", errors="ignore").strip()
    with STATS_PATH.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write("# image_stats\n\n")
            handle.write("| Время | Источник | Запрос | Найдено | Скачано | Статус | Ошибка |\n")
            handle.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        safe_error = str(error).replace("\n", " ").replace("|", "/")[:220] or "-"
        handle.write(f"| {now_text()} | Sogou Images | {query} | {found} | {downloaded} | {status} | {safe_error} |\n")


def request_headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json,text/javascript,text/html,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Referer": "https://pic.sogou.com/",
        "X-Requested-With": "XMLHttpRequest",
    }


def safe_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_image_url(value: str) -> str:
    value = unquote(str(value or "").strip())
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("/"):
        value = urljoin("https://pic.sogou.com", value)
    if not value.startswith(("http://", "https://")):
        return ""
    lowered = value.lower()
    if any(marker in lowered for marker in NEGATIVE_MARKERS):
        return ""
    return value


def extract_sogou_items(payload) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    candidates = []
    for key in ("items", "list", "results", "picList"):
        value = payload.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    if isinstance(data, dict):
        for key in ("items", "list", "results", "picList"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    elif isinstance(data, list):
        candidates.extend(data)
    return [item for item in candidates if isinstance(item, dict)]


def parse_json_images(payload) -> list[dict]:
    images: list[dict] = []
    for item in extract_sogou_items(payload):
        image_url = ""
        preview = ""
        for key in ("picUrl", "oriPicUrl", "originImage", "bthumbUrl", "thumbUrl", "url", "imgUrl"):
            image_url = clean_image_url(item.get(key) or "")
            if image_url:
                break
        for key in ("thumbUrl", "bthumbUrl", "smallThumbUrl", "picUrl"):
            preview = clean_image_url(item.get(key) or "")
            if preview:
                break
        if not image_url:
            continue
        title = safe_text(item.get("title") or item.get("alt") or item.get("fromPageTitle") or item.get("query"))
        source = safe_text(item.get("fromPageTitle") or item.get("site") or item.get("host") or item.get("fromURLHost"))
        source_url = clean_image_url(item.get("fromURL") or item.get("pageUrl") or item.get("link") or "")
        images.append(
            {
                "title": title or "Sogou image",
                "image_url": image_url,
                "source": source_url or source or "Sogou Images",
                "preview": preview or image_url,
            }
        )
    return images


def parse_html_images(text: str) -> list[dict]:
    if not BeautifulSoup:
        return []
    soup = BeautifulSoup(text or "", "html.parser")
    images: list[dict] = []
    for node in soup.select("img"):
        image_url = clean_image_url(
            node.get("data-src")
            or node.get("data-original")
            or node.get("data-imgurl")
            or node.get("src")
            or ""
        )
        if not image_url:
            continue
        title = safe_text(node.get("alt") or node.get("title") or "Sogou image")
        images.append({"title": title, "image_url": image_url, "source": "Sogou Images", "preview": image_url})
    return images


def unique_images(images: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen: set[str] = set()
    for image in images:
        url = image.get("image_url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        result.append(image)
    return result


def fetch_sogou_endpoint(session: requests.Session, url: str, query: str, count: int, start: int = 0) -> tuple[list[dict], str]:
    params = {
        "query": query,
        "mode": "1",
        "start": str(start),
        "reqType": "ajax",
        "reqFrom": "result",
        "sogou_num": str(count),
        "xml_len": str(count),
    }
    response = session.get(url, params=params, headers=request_headers(), timeout=35)
    if response.status_code in {403, 429, 500, 502, 503}:
        return [], f"{response.status_code}"
    text = response.text or ""
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict) and str(payload.get("info", "")).lower() == "forbid":
        return [], "forbid"
    images = parse_json_images(payload) if payload is not None else []
    if not images:
        images = parse_html_images(text)
    return images, ""


def download_image(image: dict, query: str, index: int, session: requests.Session) -> str:
    url = image.get("image_url") or image.get("preview") or ""
    if not url:
        return ""
    response = session.get(url, headers=request_headers(), timeout=35)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "image" not in content_type or len(response.content) < 10_000:
        return ""
    digest = hashlib.sha256(response.content).hexdigest()[:16]
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"
    safe_query = re.sub(r"[^0-9A-Za-z\u3400-\u9fff_-]+", "_", query)[:30] or "sogou"
    path = IMAGE_DIR / f"{safe_query}_{index}_{digest}{ext}"
    path.write_bytes(response.content)
    meta_path = path.with_suffix(path.suffix + ".json")
    metadata = {
        "query": query,
        "title": image.get("title", ""),
        "image_url": image.get("image_url", ""),
        "source": image.get("source", ""),
        "preview": image.get("preview", ""),
        "saved_at": now_text(),
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def save_images(images: list[dict], query: str, download_limit: int = 5) -> int:
    ensure_dirs()
    session = requests.Session()
    if CHINA_PROXY_DICT:
        session.proxies.update(CHINA_PROXY_DICT)
    downloaded = 0
    metadata = {
        "query": query,
        "saved_at": now_text(),
        "count": len(images),
        "images": images,
    }
    metadata_path = IMAGE_DIR / f"{int(time.time())}_{hashlib.sha1(query.encode('utf-8')).hexdigest()[:10]}_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    for index, image in enumerate(images[:download_limit], 1):
        try:
            if download_image(image, query, index, session):
                downloaded += 1
        except Exception:
            continue
    return downloaded


def search_sogou_images(query: str, count: int = 20, save: bool = True, download_limit: int = 5) -> list[dict]:
    ensure_dirs()
    session = requests.Session()
    if CHINA_PROXY_DICT:
        session.proxies.update(CHINA_PROXY_DICT)

    errors: list[str] = []
    images: list[dict] = []
    try:
        try:
            session.get("https://pic.sogou.com/", headers=request_headers(), timeout=20)
        except Exception:
            pass
        for url in SOGOU_IMAGE_URLS:
            found, error = fetch_sogou_endpoint(session, url, query, count)
            if found:
                images.extend(found)
                break
            if error:
                errors.append(f"{url}: {error}")
        images = unique_images(images)[:count]
        downloaded = save_images(images, query, download_limit=download_limit) if save and images else 0
        status = "ok" if images else "empty"
        log_image_stats(query, status, found=len(images), downloaded=downloaded, error=", ".join(errors))
        return images
    except Exception as exc:
        log_image_stats(query, "error", found=len(images), downloaded=0, error=str(exc))
        return images


def search_default_keywords(count: int = 20) -> dict[str, list[dict]]:
    return {query: search_sogou_images(query, count=count) for query in DEFAULT_KEYWORDS}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--download-limit", type=int, default=5)
    args = parser.parse_args()
    if args.query:
        result = search_sogou_images(args.query, count=args.count, download_limit=args.download_limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        result = search_default_keywords(count=args.count)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
