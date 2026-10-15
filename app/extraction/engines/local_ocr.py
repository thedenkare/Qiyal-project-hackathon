"""LocalOCREngine: локальный OCR без интернета. Качество заметно ниже, чем у мультимодальной модели.

Используется PaddleOCR, если установлен, иначе Tesseract (rus+kaz, если есть kaz). Табличная
структура восстанавливается эвристикой: в строке ищем хвост из чисел «кол-во [ед.] цена сумма».
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..schema import ExtractedDocument, ExtractedLine
from .base import Engine, EngineUnavailable, ExtractionFailed

NUM = r"\d[\d\s]*(?:[.,]\d+)?"
UNITS = r"шт|кг|л|уп|мешок|меш|м|компл|пара"
TOTAL_RE = re.compile(rf"(итого|всего)[^\d]*(?P<v>{NUM})", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{2})[./](\d{2})[./](\d{4})")


def _num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def _groupings(tokens: list[str]) -> list[list[float]]:
    """Все способы склеить токены в числа: «1 050» может быть 1050 или 1 и 050.
    Склеиваем только с последующими токенами ровно из 3 цифр (разряды тысяч)."""
    if not tokens:
        return [[]]
    out = []
    for k in range(1, len(tokens) + 1):
        head = tokens[:k]
        if k > 1 and not all(re.fullmatch(r"\d{3}(?:[.,]\d+)?", t) for t in head[1:]):
            break
        if any(re.search(r"[.,]", t) for t in head[:-1]):
            break
        for rest in _groupings(tokens[k:]):
            out.append([_num("".join(head))] + rest)
    return out


def split_numbers(tail_tokens: list[str]) -> tuple[float, float, float] | None:
    """Разбить хвост строки на (кол-во, цена, сумма), предпочитая вариант, где кол-во × цена = сумма."""
    best = None
    for g in _groupings(tail_tokens):
        if len(g) != 3:
            continue
        q, p, a = g
        if q > 0 and abs(q * p - a) <= max(1.0, 0.005 * a):
            return q, p, a
        best = best or (q, p, a)
    return best


def parse_line(s: str) -> ExtractedLine | None:
    m = re.match(rf"^\s*(?:\d{{1,3}}[.)]?\s+)?(?P<body>.+)$", s)
    body = m.group("body")
    tokens = body.split()
    # хвост: числа и, возможно, единица измерения между количеством и ценой
    tail, unit = [], None
    while tokens and (re.fullmatch(r"[\d.,]+", tokens[-1]) or re.fullmatch(rf"(?:{UNITS})\.?", tokens[-1], re.I)):
        t = tokens.pop()
        if re.fullmatch(r"[\d.,]+", t):
            tail.insert(0, t)
        else:
            unit = t.rstrip(".").lower()
            break
    if unit:  # перед единицей — количество (возможно, «1 000»)
        qty_tok = []
        while tokens and re.fullmatch(r"[\d.,]+", tokens[-1]):
            qty_tok.insert(0, tokens.pop())
        tail = qty_tok + tail
    if len(tail) < 3 or not tokens:
        return None
    nums = split_numbers(tail)
    if nums is None:
        return None
    q, p, a = nums
    name = " ".join(tokens).strip(" .-")
    if not re.search(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүІі]", name):
        return None
    return ExtractedLine(name=name, qty=q, unit=unit or "шт", unit_price=p, amount=a,
                         vat_rate=None, confidence=0.6, uncertain=["vat_rate"])


def parse_text(text: str) -> ExtractedDocument:
    """Эвристический разбор текста OCR в строки накладной. Каждая строка с пониженной уверенностью."""
    lines: list[ExtractedLine] = []
    total = None
    date = None
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if date is None and (m := DATE_RE.search(s)):
            date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if (m := TOTAL_RE.search(s)) and "ндс" not in s.lower():
            total = _num(m.group("v"))
            continue
        ln = parse_line(s)
        if ln:
            lines.append(ln)
    return ExtractedDocument(doc_date=date, prices_include_vat=True, lines=lines, total_gross=total)


class LocalOCREngine(Engine):
    name = "local_ocr"
    label = "Локальный OCR"
    quality_note = ("Локальный OCR: качество ниже, ставка НДС не определяется автоматически. "
                    "Проверьте каждую строку.")

    def _backend(self) -> str | None:
        try:
            import paddleocr  # noqa: F401
            return "paddle"
        except ImportError:
            pass
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return None
        return "tesseract" if shutil.which("tesseract") else None

    def available(self) -> tuple[bool, str]:
        if self._backend() is None:
            return False, "Не установлен ни PaddleOCR, ни Tesseract (pip install pytesseract + программа tesseract с языком rus)"
        return True, ""

    def _text(self, path: Path) -> str:
        from ..preprocess import prepare_for_ocr
        img = prepare_for_ocr(path)
        backend = self._backend()
        if backend == "paddle":
            import numpy as np
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(lang="ru", show_log=False)
            res = ocr.ocr(np.array(img.convert("RGB")))
            # группируем боксы в строки по вертикали
            boxes = sorted(((b[0][0][1], b[0][0][0], b[1][0]) for b in (res[0] or [])))
            rows: list[list[tuple[float, str]]] = []
            last_y = None
            for y, x, t in boxes:
                if last_y is None or abs(y - last_y) > 12:
                    rows.append([])
                rows[-1].append((x, t))
                last_y = y
            return "\n".join(" ".join(t for _, t in sorted(r)) for r in rows)
        import pytesseract
        langs = pytesseract.get_languages(config="")
        lang = "rus+kaz" if "kaz" in langs else "rus"
        if "rus" not in langs:
            raise EngineUnavailable("В Tesseract нет языка rus")
        return pytesseract.image_to_string(img, lang=lang, config="--psm 6")

    def extract(self, path: Path) -> ExtractedDocument:
        ok, reason = self.available()
        if not ok:
            raise EngineUnavailable(reason)
        doc = parse_text(self._text(path))
        if not doc.lines:
            raise ExtractionFailed("OCR не нашёл строк таблицы")
        return doc
