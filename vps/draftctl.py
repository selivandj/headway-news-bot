import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InputMediaPhoto
from telegram.constants import ParseMode

BASE_DIR = Path(__file__).resolve().parent
DRAFT_DIR = BASE_DIR / "drafts"
DRAFT_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")


def make_slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return "-".join(part for part in cleaned.split("-") if part)[:60] or "draft"


async def send_review(bot: Bot, chat_id: int, draft: dict) -> None:
    media = draft.get("media", [])
    if media:
        photos = []
        opened = []
        for index, item in enumerate(media[:10]):
            media_path = BASE_DIR / item["path"]
            handle = media_path.open("rb")
            opened.append(handle)
            caption = draft["text"] if index == 0 and len(draft["text"]) <= 1024 else None
            photos.append(InputMediaPhoto(media=handle, caption=caption))
        await bot.send_media_group(chat_id=chat_id, media=photos)
        for handle in opened:
            handle.close()

    await bot.send_message(
        chat_id=chat_id,
        text="Черновик для @headway74. Если все ок, ответь: публикуй",
    )
    await bot.send_message(
        chat_id=chat_id,
        text=draft["text"],
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True)
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--media", nargs="*", default=[])
    parser.add_argument("--send-review", action="store_true")
    args = parser.parse_args()

    text = Path(args.text_file).read_text(encoding="utf-8").strip()
    media = [{"path": item, "caption": ""} for item in args.media]
    draft = {"title": args.title, "text": text, "media": media, "created_at": int(time.time())}

    filename = f"{int(time.time())}-{make_slug(args.title)}.pending.json"
    draft_path = DRAFT_DIR / filename
    draft_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    print(draft_path)

    if args.send_review:
        bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
        await send_review(bot, int(os.environ["TELEGRAM_REVIEW_CHAT_ID"]), draft)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
