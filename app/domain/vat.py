"""Утечка НДС-зачёта и панель «Режим и порог НДС»."""
from __future__ import annotations

from dataclasses import dataclass

from .pricing import Regime


def hypothetical_credit(gross_without_credit: float, vat_rate: float) -> float:
    """Сценарий «что если»: тот же объём по той же цене, но у плательщика НДС с ЭСФ.

    Зачёт = G × v / (1 + v). Это не гарантия экономии: поставщик с НДС может стоить дороже.
    """
    if gross_without_credit <= 0 or vat_rate <= 0:
        return 0.0
    return gross_without_credit * vat_rate / (1 + vat_rate)


@dataclass(frozen=True)
class RegimeStatus:
    turnover_12m: float | None
    vat_threshold: float
    snr_limit: float
    share_of_vat_threshold: float | None
    share_of_snr_limit: float | None
    notes: list[str]


def regime_status(regime: Regime, turnover_12m: float | None, vat_threshold: float, snr_limit: float) -> RegimeStatus:
    notes: list[str] = []
    share_vat = turnover_12m / vat_threshold if turnover_12m is not None else None
    share_snr = turnover_12m / snr_limit if turnover_12m is not None else None
    if turnover_12m is None:
        notes.append("Оборот за 12 месяцев не введён: импортируйте продажи или введите вручную в настройках.")
    if regime == Regime.SNR:
        notes.append("По найденным источникам на упрощённой декларации постановка на учёт по НДС не предусмотрена; "
                     "входной НДС в зачёт не берётся, себестоимость = всё уплаченное поставщику.")
        if share_snr is not None and share_snr >= 1:
            notes.append("Оборот превышает лимит упрощённой декларации: по источникам требуется переход на ОУР.")
        elif share_snr is not None and share_snr >= 0.8:
            notes.append("Оборот выше 80% лимита упрощённой декларации.")
    else:
        if share_vat is not None and share_vat >= 1:
            notes.append("Оборот выше порога обязательной постановки на учёт по НДС.")
        notes.append("На ОУР себестоимость зависит от структуры поставщиков: закупки без НДС/ЭСФ не дают зачёта.")
    return RegimeStatus(turnover_12m, vat_threshold, snr_limit, share_vat, share_snr, notes)
