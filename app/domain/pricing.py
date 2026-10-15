"""Себестоимость, маржа и рекомендуемая цена.

Чистая логика без I/O. Формулы из раздела 6 спецификации:

ОУР (плательщик НДС):
    маржа = (P/(1+v_sale) − cost) / (P/(1+v_sale)),   P = cost/(1−m) × (1+v_sale)
СНР (упрощёнка, неплательщик НДС):
    маржа = (P×(1−t) − cost) / P,                      P = cost / (1 − t − m)

Маржа считается до подоходного налога и накладных расходов.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Regime(str, Enum):
    OUR = "our"  # общеустановленный режим, плательщик НДС
    SNR = "snr"  # упрощённая декларация, не плательщик НДС


class PricingError(ValueError):
    """Расчёт невозможен при данных параметрах (например, t + m ≥ 100%)."""


@dataclass(frozen=True)
class Profile:
    regime: Regime
    target_margin: float = 0.25
    margin_floor: float = 0.15
    snr_rate: float = 0.04
    sale_vat_rate: float = 0.16
    rounding_step: int = 10

    @property
    def out_vat(self) -> float:
        """НДС, который сидит внутри цены на полке."""
        return self.sale_vat_rate if self.regime == Regime.OUR else 0.0


# --- строка накладной -------------------------------------------------------

def normalize_unit_price(unit_price: float, vat_rate: float, price_includes_vat: bool) -> tuple[float, float]:
    """Вернуть (unit_net, unit_gross) по цене из документа."""
    if unit_price < 0:
        raise PricingError("Цена не может быть отрицательной")
    if vat_rate < 0:
        raise PricingError("Ставка НДС не может быть отрицательной")
    if price_includes_vat:
        gross = unit_price
        net = unit_price / (1 + vat_rate)
    else:
        net = unit_price
        gross = unit_price * (1 + vat_rate)
    return net, gross


def credit_available(regime: Regime, line_vat_rate: float, supplier_vat_payer: bool, has_esf: bool) -> bool:
    """Можно ли взять входной НДС в зачёт по этой строке."""
    return regime == Regime.OUR and supplier_vat_payer and has_esf and line_vat_rate > 0


def effective_cost(unit_net: float, vat_rate: float, regime: Regime,
                   supplier_vat_payer: bool, has_esf: bool) -> float:
    """Себестоимость единицы для бизнеса с учётом зачёта НДС."""
    gross = unit_net * (1 + vat_rate)
    if credit_available(regime, vat_rate, supplier_vat_payer, has_esf):
        return unit_net
    return gross


# --- маржа и цена ----------------------------------------------------------

def margin(price: float, cost: float, profile: Profile) -> float | None:
    """Маржа при цене на полке `price`. None, если цена не задана."""
    if price is None or price <= 0:
        return None
    if profile.regime == Regime.OUR:
        net = price / (1 + profile.out_vat)
        return (net - cost) / net
    return (price * (1 - profile.snr_rate) - cost) / price


def raw_price_for_margin(cost: float, m: float, profile: Profile) -> float:
    if cost <= 0:
        raise PricingError("Себестоимость должна быть больше нуля")
    if profile.regime == Regime.OUR:
        if m >= 1:
            raise PricingError("Маржа должна быть меньше 100%")
        return cost / (1 - m) * (1 + profile.out_vat)
    denom = 1 - profile.snr_rate - m
    if denom <= 0:
        raise PricingError("Ставка налога + целевая маржа должны быть меньше 100%")
    return cost / denom


def round_up(value: float, step: int) -> float:
    if step <= 0:
        return value
    # защита от 1550.0000000002 → 1560
    return math.ceil(round(value / step, 9)) * step


def round_down(value: float, step: int) -> float:
    if step <= 0:
        return value
    return math.floor(round(value / step, 9)) * step


def max_price_for_markup(cost: float, max_markup_pct: float, profile: Profile) -> float:
    """Потолок цены на полке при ограничении наценки к себестоимости (на выручку без НДС)."""
    return cost * (1 + max_markup_pct) * (1 + profile.out_vat)


def markup(price: float, cost: float, profile: Profile) -> float:
    net = price / (1 + profile.out_vat)
    return net / cost - 1


@dataclass(frozen=True)
class Recommendation:
    price: float            # округлённая рекомендуемая цена (с учётом потолка)
    raw: float              # точная цена под целевую маржу
    low: float              # цена под пол маржи (нижняя граница диапазона)
    margin: float           # маржа при рекомендуемой цене
    capped: bool            # упёрлись в max_markup_pct
    cap_price: float | None


def recommend(cost: float, profile: Profile, max_markup_pct: float | None = None) -> Recommendation:
    raw = raw_price_for_margin(cost, profile.target_margin, profile)
    price = round_up(raw, profile.rounding_step)
    low = round_up(raw_price_for_margin(cost, min(profile.margin_floor, profile.target_margin), profile),
                   profile.rounding_step)
    capped = False
    cap_price = None
    if max_markup_pct is not None:
        cap_price = max_price_for_markup(cost, max_markup_pct, profile)
        cap_rounded = round_down(cap_price, profile.rounding_step)
        if price > cap_price:
            price = cap_rounded
            capped = True
        low = min(low, price)
    return Recommendation(price=price, raw=raw, low=low, margin=margin(price, cost, profile),
                          capped=capped, cap_price=cap_price)
