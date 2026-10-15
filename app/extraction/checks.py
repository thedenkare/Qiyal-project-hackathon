"""Арифметические проверки — главный щит от ошибок распознавания.

Проверяем, что числа документа согласованы между собой. Несовпадение подсвечивает
ячейку и снижает уверенность строки. Ничего не «исправляем» молча.
"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import ExtractedDocument


@dataclass(frozen=True)
class Issue:
    line: int | None   # индекс строки или None для итогов
    field: str         # qty / unit_price / amount / vat_amount / total_net / total_vat / total_gross
    message: str


def fmt(v: float) -> str:
    """1234567.5 → «1 234 567,50», целые без копеек."""
    s = f"{v:,.2f}".replace(",", "\u202f").replace(".", ",")
    return s[:-3] if s.endswith(",00") else s


def close(a: float, b: float, abs_tol: float = 1.0, rel_tol: float = 0.005) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def check_document(doc: ExtractedDocument, abs_tol: float = 1.0, rel_tol: float = 0.005,
                   penalty: float = 0.4) -> list[Issue]:
    """Вернуть найденные расхождения и понизить confidence затронутых строк (мутирует doc)."""
    issues: list[Issue] = []

    def ok(a, b):
        return close(a, b, abs_tol, rel_tol)

    for i, ln in enumerate(doc.lines):
        missing = [f for f in ("qty", "unit_price", "amount") if getattr(ln, f) is None]
        for f in missing:
            issues.append(Issue(i, f, "Не распознано"))
        if ln.qty is not None and ln.qty <= 0:
            issues.append(Issue(i, "qty", "Количество должно быть больше нуля"))
        if ln.unit_price is not None and ln.unit_price < 0:
            issues.append(Issue(i, "unit_price", "Отрицательная цена"))
        if not missing and not ok(ln.qty * ln.unit_price, ln.amount):
            issues.append(Issue(i, "amount", f"Кол-во × цена = {fmt(ln.qty * ln.unit_price)}, "
                                             f"а в документе {fmt(ln.amount)}"))
        if ln.vat_rate and ln.vat_amount is not None and ln.amount is not None:
            base = ln.amount / (1 + ln.vat_rate) if doc.prices_include_vat else ln.amount
            if not ok(base * ln.vat_rate, ln.vat_amount):
                issues.append(Issue(i, "vat_amount", f"НДС по ставке = {fmt(base * ln.vat_rate)}, "
                                                     f"в документе {fmt(ln.vat_amount)}"))

    amounts = [ln.amount for ln in doc.lines if ln.amount is not None]
    if amounts and len(amounts) == len(doc.lines):
        s = sum(amounts)
        target_field = "total_gross" if doc.prices_include_vat else "total_net"
        target = getattr(doc, target_field)
        if target is not None and not ok(s, target):
            issues.append(Issue(None, target_field, f"Сумма строк {fmt(s)} ≠ итого {fmt(target)}"))
    vats = [ln.vat_amount for ln in doc.lines if ln.vat_amount is not None]
    if vats and len(vats) == len(doc.lines) and doc.total_vat is not None and not ok(sum(vats), doc.total_vat):
        issues.append(Issue(None, "total_vat", f"Сумма НДС строк {fmt(sum(vats))} ≠ итого НДС "
                                               f"{fmt(doc.total_vat)}"))
    if None not in (doc.total_net, doc.total_vat, doc.total_gross) and \
            not ok(doc.total_net + doc.total_vat, doc.total_gross):
        issues.append(Issue(None, "total_gross", "Итого без НДС + НДС ≠ итого к оплате"))

    for iss in issues:
        if iss.line is not None:
            ln = doc.lines[iss.line]
            ln.confidence = round(max(0.0, ln.confidence - penalty), 2)
            if iss.field not in ln.uncertain:
                ln.uncertain.append(iss.field)
    return issues
