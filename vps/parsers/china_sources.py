from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser


CHINA_EV_KEYWORDS = (
    "charging",
    "charger",
    "supercharger",
    "fast charge",
    "ultra-fast",
    "battery swap",
    "swap station",
    "evse",
    "electric vehicle",
    "nev",
    "заряд",
    "электромоб",
    "充电",
    "换电",
)


@dataclass(frozen=True)
class ChinaSource:
    name: str
    url: str
    language: str
    priority: str


class ChinaSourcesParser:
    """Free RSS parser for China EV sources.

    This is intentionally RSS-only. Direct WeChat scraping is not included here:
    it is brittle and should stay behind a dedicated, reviewed collector.
    """

    SOURCES = (
        ChinaSource("cnevpost", "https://cnevpost.com/feed/", "en", "high"),
        ChinaSource("carnewschina", "https://carnewschina.com/feed/", "en", "high"),
        ChinaSource("chinadaily_auto", "http://www.chinadaily.com.cn/rss/auto/rss.xml", "en", "medium"),
        ChinaSource(
            "google_news_china_ev",
            "https://news.google.com/rss/search?q=EV+charging+China&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
            "zh",
            "medium",
        ),
    )

    def parse_all(self, per_source: int = 10) -> list[dict]:
        articles: list[dict] = []
        for source in self.SOURCES:
            articles.extend(self.parse_source(source, per_source=per_source))
        return self.dedupe(articles)

    def parse_source(self, source: ChinaSource, per_source: int = 10) -> list[dict]:
        try:
            feed = feedparser.parse(source.url)
        except Exception:
            return []

        articles: list[dict] = []
        for entry in feed.entries[:per_source]:
            article = self.process_entry(entry, source)
            if article:
                articles.append(article)
        return articles

    def process_entry(self, entry, source: ChinaSource) -> dict | None:
        title = str(getattr(entry, "title", "") or "").strip()
        link = str(getattr(entry, "link", "") or "").strip()
        summary = str(getattr(entry, "summary", "") or "").strip()
        if not title or not link:
            return None
        if not self.is_relevant(title, summary):
            return None

        return {
            "id": hashlib.sha256(link.encode("utf-8")).hexdigest()[:16],
            "title": title,
            "link": link,
            "summary": summary[:700],
            "published": str(getattr(entry, "published", "") or datetime.now(timezone.utc).isoformat()),
            "source": source.name,
            "region": "China",
            "language": source.language,
            "priority": source.priority,
        }

    @staticmethod
    def is_relevant(title: str, summary: str) -> bool:
        haystack = f"{title} {summary}".lower()
        return any(keyword.lower() in haystack for keyword in CHINA_EV_KEYWORDS)

    @staticmethod
    def dedupe(articles: list[dict]) -> list[dict]:
        result: list[dict] = []
        seen: set[str] = set()
        for article in articles:
            key = article.get("link") or article.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(article)
        return result

