"""Предобработка фото: ориентация по EXIF, выравнивание наклона, контраст, размер."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

MAX_SIDE = 2000


class BadImage(ValueError):
    pass


def open_image(path: Path) -> Image.Image:
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:  # Pillow бросает разные исключения на битые файлы
        raise BadImage("Файл не читается как изображение") from e
    return ImageOps.exif_transpose(img)


def _ink_profile_score(gray: Image.Image, angle: float) -> float:
    """Чем резче «полосы» строк по горизонтали, тем ровнее текст."""
    rot = gray.rotate(angle, expand=False, fillcolor=255)
    w, h = rot.size
    px = rot.load()
    rows = []
    for y in range(h):
        dark = 0
        for x in range(0, w, 2):
            if px[x, y] < 128:
                dark += 1
        rows.append(dark)
    mean = sum(rows) / len(rows)
    return sum((r - mean) ** 2 for r in rows)


def estimate_skew(img: Image.Image, max_angle: float = 8.0, step: float = 1.0) -> float:
    """Грубая оценка наклона перебором углов на уменьшенной копии."""
    small = ImageOps.grayscale(img)
    small.thumbnail((300, 300))
    small = ImageOps.autocontrast(small)
    best, best_score = 0.0, -1.0
    a = -max_angle
    while a <= max_angle + 1e-9:
        s = _ink_profile_score(small, a)
        if s > best_score:
            best, best_score = a, s
        a += step
    return best


def prepare_for_model(path: Path) -> Image.Image:
    img = open_image(path).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def prepare_for_ocr(path: Path) -> Image.Image:
    img = open_image(path).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    angle = estimate_skew(img)
    if abs(angle) >= 0.5:
        img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    gray = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    return gray.filter(ImageFilter.SHARPEN)
