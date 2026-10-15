"""Загрузка конфигов. Все налоговые и продуктовые числа живут в config/*.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("MARGINGUARD_DATA_DIR", ROOT / "data"))
DEMO_DIR = ROOT / "demo_data"

load_dotenv(ROOT / ".env")


@lru_cache
def tax_params() -> dict:
    return json.loads((CONFIG_DIR / "tax_params_2026.json").read_text(encoding="utf-8"))


@lru_cache
def app_settings() -> dict:
    return json.loads((CONFIG_DIR / "app_settings.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class TaxNumbers:
    """Плоский набор чисел, которые нужны доменной логике."""

    mrp: int
    vat_rate: float
    vat_threshold_mrp: int
    snr_limit_mrp: int
    snr_base_rate: float
    snr_rate_min: float
    snr_rate_max: float
    nonfood_mom: float

    @property
    def vat_threshold_tenge(self) -> int:
        return self.mrp * self.vat_threshold_mrp

    @property
    def snr_limit_tenge(self) -> int:
        return self.mrp * self.snr_limit_mrp


def tax_numbers() -> TaxNumbers:
    p = tax_params()
    return TaxNumbers(
        mrp=p["mrp_2026"]["value"],
        vat_rate=p["vat_standard_rate"]["value"],
        vat_threshold_mrp=p["vat_registration_threshold_mrp"]["value"],
        snr_limit_mrp=p["snr_simplified"]["income_limit_mrp"],
        snr_base_rate=p["snr_simplified"]["base_rate"],
        snr_rate_min=p["snr_simplified"]["rate_min"],
        snr_rate_max=p["snr_simplified"]["rate_max"],
        nonfood_mom=p["market_benchmark"]["nonfood_mom"],
    )


def source(key: str) -> dict:
    """Источник для подписи в UI: url + дата проверки."""
    item = tax_params().get(key, {})
    return {"url": item.get("source_url", ""), "verified": item.get("last_verified", ""),
            "status": item.get("status", "")}
