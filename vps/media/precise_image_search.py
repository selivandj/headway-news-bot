from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from duckduckgo_search import DDGS
except Exception:  # pragma: no cover - optional dependency on VPS
    DDGS = None

try:
    from PIL import Image
except Exception:  # pragma: no cover - image verification is best-effort
    Image = None

try:
    from media.image_dedup import ImageDeduplicator
except Exception:  # pragma: no cover - optional safe stage dependency
    ImageDeduplicator = None


GENERIC_ENTITY_TERMS = {
    "image",
    "photo",
    "charger",
    "charging",
    "station",
    "ev",
    "эзс",
    "фото",
    "картинка",
    "изображение",
    "заряд",
    "зарядка",
    "станция",
    "электромобиль",
}

UNSAFE_URL_MARKERS = (
    "adult",
    "avatar",
    "badge",
    "banner",
    "blank",
    "escort",
    "favicon",
    "icon",
    "logo",
    "placeholder",
    "porn",
    "profile",
    "qr",
    "qrcode",
    "sex",
    "sprite",
    "thumb",
    "thumbnail",
    "userpic",
)

SAFESEARCH_REGIONS = ("ru-ru", "wt-wt", "us-en")


@dataclass(frozen=True)
class PreciseImage:
    path: str
    original_url: str
    source_query: str
    entity: str
    title: str = ""
    source_page: str = ""


class PreciseImageSearch:
    """Strict image search for a specific news entity.

    The class intentionally avoids generic queries like "charging station".
    Every query must include a concrete entity from the draft or owner command:
    station model, company, product name, event, or city/project name.
    """

    def __init__(
        self,
        download_dir: str | Path = "vps/media/storage/images",
        min_file_size_bytes: int = 20_000,
        max_file_size_bytes: int = 8_000_000,
        timeout_seconds: int = 15,
    ):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.min_file_size_bytes = min_file_size_bytes
        self.max_file_size_bytes = max_file_size_bytes
        self.timeout_seconds = timeout_seconds
        self.deduplicator = None
        if ImageDeduplicator and os.environ.get("IMAGE_HASH_ENABLED", "1").lower() in {"1", "true", "yes", "on"}:
            vps_dir = Path(__file__).resolve().parents[1]
            threshold = int(os.environ.get("IMAGE_HASH_THRESHOLD") or "10")
            self.deduplicator = ImageDeduplicator(vps_dir / "database" / "image_hashes.db", threshold=threshold)

    def search_by_entities(self, entities: list[str], draft_id: str, max_images: int = 2) -> dict:
        precise_entities = self._meaningful_entities(entities)
        if not precise_entities:
            return {
                "status": "no_entities",
                "message": "Не нашел точную сущность для поиска фото: нужна модель станции, компания, продукт или событие.",
                "images": [],
            }
        if DDGS is None:
            return {
                "status": "dependency_missing",
                "message": "Не установлен duckduckgo-search, точный поиск изображений сейчас недоступен.",
                "images": [],
            }

        found: list[PreciseImage] = []
        seen_urls: set[str] = set()
        seen_hashes: set[str] = set()

        for query, entity in self.build_smart_queries(precise_entities):
            if len(found) >= max_images:
                break
            for item in self._ddg_images(query):
                image_url = self._image_url_from_item(item)
                if not image_url or image_url in seen_urls:
                    continue
                if not self._is_safe_image_url(image_url):
                    continue
                if not self._metadata_matches(item, entity):
                    continue

                saved = self.download_image(image_url, draft_id=draft_id, seen_hashes=seen_hashes)
                if not saved:
                    seen_urls.add(image_url)
                    continue

                found.append(
                    PreciseImage(
                        path=saved,
                        original_url=image_url,
                        source_query=query,
                        entity=entity,
                        title=str(item.get("title") or ""),
                        source_page=str(item.get("url") or item.get("source") or ""),
                    )
                )
                seen_urls.add(image_url)
                if len(found) >= max_images:
                    break

        if not found:
            return {
                "status": "not_found",
                "message": (
                    "Точного изображения не нашел. Можно уточнить модель/компанию в ответе на черновик "
                    "или дать отдельную команду на нейтральную генерацию."
                ),
                "images": [],
            }

        return {
            "status": "success",
            "message": f"Найдено точных изображений: {len(found)}.",
            "images": [asdict(item) for item in found],
        }

    def build_smart_queries(self, entities: list[str], max_queries: int = 12) -> list[tuple[str, str]]:
        precise_entities = self._meaningful_entities(entities)
        queries: list[tuple[str, str]] = []

        if len(precise_entities) >= 2:
            combined = f'"{precise_entities[0]}" "{precise_entities[1]}"'
            queries.extend(
                [
                    (f"{combined} фото", precise_entities[0]),
                    (f"{combined} зарядная станция", precise_entities[0]),
                    (f"{combined} EV charger", precise_entities[0]),
                ]
            )

        for entity in precise_entities[:4]:
            quoted = f'"{entity}"'
            queries.extend(
                [
                    (f"{quoted} фото", entity),
                    (f"{quoted} зарядная станция фото", entity),
                    (f"{quoted} ЭЗС фото", entity),
                    (f"{quoted} EV charger photo", entity),
                    (f"{quoted} charging station photo", entity),
                    (f"{quoted} выставка релиз", entity),
                ]
            )

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for query, entity in queries:
            key = query.lower()
            if key not in seen:
                seen.add(key)
                unique.append((query, entity))
            if len(unique) >= max_queries:
                break
        return unique

    def download_image(self, url: str, draft_id: str, seen_hashes: set[str] | None = None) -> str | None:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 HeadwayNewsBot/1.0"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except Exception:
            return None

        content_type = (response.headers.get("content-type") or "").lower()
        if "image" not in content_type:
            return None
        if "svg" in content_type or "gif" in content_type:
            return None

        content = response.content
        if len(content) < self.min_file_size_bytes or len(content) > self.max_file_size_bytes:
            return None

        fingerprint = hashlib.sha256(content).hexdigest()
        if seen_hashes is not None:
            if fingerprint in seen_hashes:
                return None
            seen_hashes.add(fingerprint)

        if not self._has_valid_dimensions(content):
            return None

        suffix = ".jpg"
        if "png" in content_type:
            suffix = ".png"
        elif "webp" in content_type:
            suffix = ".webp"

        file_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        safe_draft_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", draft_id)[:40] or "draft"
        path = self.download_dir / f"{safe_draft_id}_{file_hash}{suffix}"
        path.write_bytes(content)
        if self.deduplicator:
            try:
                result = self.deduplicator.check_and_register(path, source_url=url, draft_id=draft_id)
                if result.get("duplicate"):
                    path.unlink(missing_ok=True)
                    logging.info("Skipped duplicate image from %s for draft %s", url, draft_id)
                    return None
            except Exception:
                logging.exception("ImageHash duplicate check failed")
        return str(path)

    def _ddg_images(self, query: str) -> list[dict]:
        if DDGS is None:
            return []
        results: list[dict] = []
        for region in SAFESEARCH_REGIONS:
            try:
                with DDGS() as ddgs:
                    for item in ddgs.images(
                        query,
                        max_results=8,
                        region=region,
                        safesearch="on",
                        size="Large",
                        type_image="photo",
                    ):
                        results.append(dict(item))
            except Exception:
                continue
            if results:
                break
        return results

    @staticmethod
    def _image_url_from_item(item: dict) -> str:
        for key in ("image", "thumbnail"):
            value = str(item.get(key) or "").strip()
            if value.startswith("//"):
                value = "https:" + value
            parsed = urlparse(value)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return value
        return ""

    @staticmethod
    def _meaningful_entities(entities: list[str]) -> list[str]:
        result: list[str] = []
        for raw in entities or []:
            entity = re.sub(r"\s+", " ", str(raw or "")).strip(" .,:;!?\"'")
            if not entity:
                continue
            if len(entity) < 3:
                continue
            if entity.lower() in GENERIC_ENTITY_TERMS:
                continue
            if entity not in result:
                result.append(entity)
        return result

    @staticmethod
    def _is_safe_image_url(url: str) -> bool:
        lowered = url.lower()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        return not any(marker in lowered for marker in UNSAFE_URL_MARKERS)

    @staticmethod
    def _metadata_matches(item: dict, entity: str) -> bool:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "url", "source", "image", "thumbnail")
        ).lower()
        if any(marker in haystack for marker in UNSAFE_URL_MARKERS):
            return False

        entity_tokens = [
            token.lower()
            for token in re.findall(r"[a-zа-яё0-9\u3400-\u9fff-]{3,}", entity, flags=re.I)
            if token.lower() not in GENERIC_ENTITY_TERMS
        ]
        if not entity_tokens:
            return False

        return any(token in haystack for token in entity_tokens)

    @staticmethod
    def _has_valid_dimensions(content: bytes) -> bool:
        if Image is None:
            return True
        try:
            from io import BytesIO

            image = Image.open(BytesIO(content))
            width, height = image.size
            return width >= 420 and height >= 260
        except Exception:
            return False
