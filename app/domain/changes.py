"""Рост закупочных цен, статусы алертов, оценка потерь."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Severity(str, Enum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    NEW = "new"  # истории закупок нет — сравнивать не с чем


@dataclass(frozen=True)
class HistoryPoint:
    day: date
    cost: float  # эффективная себестоимость единицы


@dataclass(frozen=True)
class PriceChange:
    new_cost: float
    ref_cost: float | None          # последняя закупка у того же поставщика
    ref_median3: float | None       # медиана трёх последних
    ref_date: date | None
    delta_pct: float | None         # относительно последней закупки
    delta_median_pct: float | None
    months: float | None            # сколько месяцев прошло с прошлой закупки
    monthly_pct: float | None       # эквивалентный рост в месяц (сложный процент)


def pct_change(new: float, ref: float | None) -> float | None:
    if ref is None or ref <= 0:
        return None
    return (new - ref) / ref


def months_between(a: date, b: date) -> float:
    return max((b - a).days, 0) / 30.4375


def price_change(new_cost: float, new_day: date, history: list[HistoryPoint]) -> PriceChange:
    """history — прошлые закупки этого товара у этого поставщика (любой порядок, без текущей)."""
    past = sorted((h for h in history if h.day <= new_day), key=lambda h: h.day)
    if not past:
        return PriceChange(new_cost, None, None, None, None, None, None, None)
    last = past[-1]
    med = statistics.median(h.cost for h in past[-3:])
    delta = pct_change(new_cost, last.cost)
    months = months_between(last.day, new_day)
    monthly = None
    if delta is not None and months >= 0.5:
        monthly = (1 + delta) ** (1 / months) - 1
    return PriceChange(new_cost, last.cost, med, last.day, delta, pct_change(new_cost, med), months, monthly)


def severity(change: PriceChange, margin_after: float | None, margin_floor: float,
             warn_pct: float, critical_pct: float) -> Severity:
    if change.delta_pct is None:
        if margin_after is not None and margin_after < margin_floor:
            return Severity.CRITICAL
        return Severity.NEW
    if change.delta_pct >= critical_pct or (margin_after is not None and margin_after < margin_floor):
        return Severity.CRITICAL
    if change.delta_pct >= warn_pct:
        return Severity.WARN
    return Severity.OK


def monthly_loss(delta_cost_unit: float, monthly_units: float | None) -> float | None:
    """Сколько маржи уходит в месяц, если цену на полке не трогать. None — продаж нет, не выдумываем."""
    if monthly_units is None or monthly_units <= 0:
        return None
    return max(delta_cost_unit, 0) * monthly_units


def faster_than_market(monthly_pct: float | None, benchmark_mom: float) -> bool | None:
    if monthly_pct is None:
        return None
    return monthly_pct > benchmark_mom
