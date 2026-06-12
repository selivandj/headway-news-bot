from __future__ import annotations

import sys
from pathlib import Path

import pytest


pytest.importorskip("imagehash")
pytest.importorskip("PIL")

from PIL import Image, ImageDraw  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
VPS_DIR = ROOT / "vps" if (ROOT / "vps").exists() else ROOT
sys.path.insert(0, str(VPS_DIR))

from media.image_dedup import ImageDeduplicator  # noqa: E402


def make_image(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (128, 128), color).save(path)


def make_pattern(path: Path, diagonal: bool) -> None:
    image = Image.new("RGB", (128, 128), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    if diagonal:
        draw.line((0, 0, 128, 128), fill=(0, 0, 0), width=12)
    else:
        draw.rectangle((20, 20, 108, 108), outline=(0, 0, 0), width=12)
    image.save(path)


def test_image_hash_detects_similar_images(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_image(first, (20, 80, 160))
    make_image(second, (20, 80, 160))

    dedup = ImageDeduplicator(tmp_path / "hashes.db", threshold=3)
    first_result = dedup.check_and_register(first, source_url="https://example.com/1", draft_id="a")
    second_result = dedup.check_and_register(second, source_url="https://example.com/2", draft_id="b")

    assert first_result["duplicate"] is False
    assert first_result["registered"] is True
    assert second_result["duplicate"] is True


def test_image_hash_keeps_distinct_images(tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    make_pattern(first, diagonal=True)
    make_pattern(second, diagonal=False)

    dedup = ImageDeduplicator(tmp_path / "hashes.db", threshold=3)
    dedup.check_and_register(first, draft_id="a")
    second_result = dedup.check_and_register(second, draft_id="b")

    assert second_result["duplicate"] is False
    assert second_result["registered"] is True
