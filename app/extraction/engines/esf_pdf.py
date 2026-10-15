"""Разбор PDF с текстовым слоем (печатная форма ЭСФ / счёт-фактура) без OCR.

Детерминированно: pdfplumber достаёт таблицы, колонки находим по ключевым словам заголовка.
ВНИМАНИЕ: проверено только на синтетических таблицах; на реальной печатной форме ЭСФ не проверялось.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..schema import ExtractedDocument, ExtractedLine
from .base import Engine, ExtractionFailed

COLS = {
    "name": ("наименование", "товар", "атауы"),
    "unit": ("ед", "единица", "өлшем"),
    "qty": ("кол", "количество", "саны"),
    "unit_price": ("цена", "бағасы"),
    "net": ("стоимость без", "без ндс", "құнсыз", "ққс-сыз"),
    "vat_rate": ("ставка",),
    "vat_amount": ("сумма ндс", "сумма ққс", "ндс сумма"),
    "gross": ("с учетом ндс", "с ндс", "ққс ескеріле", "всего"),
}


def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace("\xa0", " ").replace(" ", "").replace(",", ".").replace("%", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _map_header(header: list[str | None]) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx, cell in enumerate(header):
        c = (cell or "").lower().replace("\n", " ")
        for key in ("vat_amount", "net", "gross", "vat_rate", "unit_price", "qty", "unit", "name"):
            if key in found:
                continue
            if any(k in c for k in COLS[key]):
                found[key] = idx
                break
    return found


def parse_tables(tables: list[list[list[str | None]]]) -> ExtractedDocument:
    lines: list[ExtractedLine] = []
    for table in tables:
        if not table:
            continue
        cols = _map_header(table[0])
        if not {"name", "qty"} <= cols.keys() or not ({"unit_price", "net", "gross"} & cols.keys()):
            continue
        for row in table[1:]:
            get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
            name = (get("name") or "").strip()
            qty = _num(get("qty"))
            if not name or qty is None or name.lower().startswith(("итого", "всего")):
                continue
            rate = _num(get("vat_rate"))
            rate = rate / 100 if rate is not None and rate > 1 else rate
            net, gross = _num(get("net")), _num(get("gross"))
            price = _num(get("unit_price"))
            # строки ЭСФ: цена без НДС, стоимость без НДС, ставка, сумма НДС, стоимость с НДС
            lines.append(ExtractedLine(name=name, qty=qty, unit=(get("unit") or "шт").strip(),
                                       unit_price=price if price is not None else (net / qty if net else None),
                                       amount=net if net is not None else gross,
                                       vat_rate=rate or 0.0, vat_amount=_num(get("vat_amount")),
                                       confidence=0.95))
    net_total = sum(ln.amount or 0 for ln in lines)
    vat_total = sum(ln.vat_amount or 0 for ln in lines)
    return ExtractedDocument(prices_include_vat=False, lines=lines,
                             total_net=round(net_total, 2) if lines else None,
                             total_vat=round(vat_total, 2) if lines else None)


class EsfPdfEngine(Engine):
    name = "esf_pdf"
    label = "PDF (текстовый слой)"
    quality_note = "Прочитано из текстового слоя PDF без OCR. Проверено на синтетических примерах, не на реальных ЭСФ."

    @staticmethod
    def has_text(path: Path) -> bool:
        import pdfplumber
        try:
            with pdfplumber.open(path) as pdf:
                return any((p.extract_text() or "").strip() for p in pdf.pages[:3])
        except Exception:
            return False

    def extract(self, path: Path) -> ExtractedDocument:
        import pdfplumber
        try:
            with pdfplumber.open(path) as pdf:
                tables = [t for p in pdf.pages for t in p.extract_tables()]
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e:
            raise ExtractionFailed("PDF повреждён или зашифрован") from e
        doc = parse_tables(tables)
        if not doc.lines:
            raise ExtractionFailed("В PDF не нашлась таблица товаров")
        if m := re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text):
            doc.doc_date = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        if m := re.search(r"(?:БИН|ИИН)[^\d]{0,20}(\d{12})", text):
            doc.supplier_bin = m.group(1)
        return doc
