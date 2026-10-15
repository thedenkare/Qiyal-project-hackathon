"""Сопоставление строк накладной с каталогом: нормализация + rapidfuzz + алиасы."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz, process

# латинские буквы, которые выглядят как кириллица (частая ошибка набора и OCR)
_LAT2CYR = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
                          "k": "к", "m": "м", "t": "т", "b": "в", "h": "н"})

_UNITS = [
    (r"\bкилограмм(ов|а)?\b|\bkg\b|\bкг\.?", "кг"),
    (r"\bграмм(ов|а)?\b|\bгр\.?\b|\bг\.(?=\s|$)", "г"),
    (r"\bлитр(ов|а)?\b|\bl\b|\bл\.?(?=\s|$)", "л"),
    (r"\bмиллилитр(ов|а)?\b|\bml\b|\bмл\.?", "мл"),
    (r"\bштук(а|и)?\b|\bшт\.?(?=\s|$)", "шт"),
    (r"\bупаковк(а|и)\b|\bуп\.?(?=\s|$)", "уп"),
    (r"\bмешок\b|\bмеш\.?(?=\s|$)", "мешок"),
    (r"\bметр(ов|а)?\b|\bм\.(?=\s|$)", "м"),
]

_ABBREV = {
    "шпатл": "шпаклевка", "шпакл": "шпаклевка", "водоэмульс": "водоэмульсионная", "в/э": "водоэмульсионная",
    "эмульс": "водоэмульсионная", "оцинк": "оцинкованный", "монт": "монтажная", "самор": "саморез",
}


def normalize(text: str) -> str:
    s = text.lower().replace("ё", "е")
    s = s.translate(_LAT2CYR) if re.search(r"[а-я]", s) else s
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)                # 2,5 → 2.5
    s = re.sub(r"(\d)\s*(?=[а-яa-z])", r"\1 ", s)         # 25кг → 25 кг
    for pat, rep in _UNITS:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[\"'«»().,;:№#*/\\-]+", " ", s)
    words = []
    for w in s.split():
        for ab, full in _ABBREV.items():
            if w.startswith(ab) and len(w) < len(full):
                w = full
                break
        words.append(w)
    return " ".join(words)


def pack_size(text: str) -> tuple[float, str] | None:
    """Фасовка из названия: «Цемент 50 кг» → (50, 'кг')."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(кг|г|л|мл|м|шт)\b", normalize(text))
    return (float(m.group(1)), m.group(2)) if m else None


@dataclass(frozen=True)
class Candidate:
    product_id: int
    name: str
    score: float
    via_alias: bool = False


def candidates(query: str, products: dict[int, str], aliases: dict[str, int] | None = None,
               limit: int = 3) -> list[Candidate]:
    """Лучшие кандидаты из каталога. Точный алиас поставщика побеждает всегда."""
    nq = normalize(query)
    if aliases and nq in aliases and aliases[nq] in products:
        pid = aliases[nq]
        return [Candidate(pid, products[pid], 100.0, via_alias=True)]
    if not products:
        return []
    choices = {pid: normalize(name) for pid, name in products.items()}
    res = process.extract(nq, choices, scorer=fuzz.token_set_ratio, limit=limit * 2)
    q_pack = pack_size(query)
    out = []
    for _norm, score, pid in res:
        p_pack = pack_size(products[pid])
        if q_pack and p_pack and q_pack != p_pack:
            score -= 25  # другая фасовка — это другой товар
        out.append(Candidate(pid, products[pid], float(score)))
    out.sort(key=lambda c: -c.score)
    return out[:limit]
