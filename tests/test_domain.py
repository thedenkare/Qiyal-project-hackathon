from datetime import date

import pytest

from app.domain import changes as ch
from app.domain.pricing import (PricingError, Profile, Regime, effective_cost, margin, normalize_unit_price,
                                raw_price_for_margin, recommend, round_up)
from app.domain.vat import hypothetical_credit, regime_status

OUR = Profile(regime=Regime.OUR, target_margin=0.25, margin_floor=0.15, sale_vat_rate=0.16)
SNR = Profile(regime=Regime.SNR, target_margin=0.25, margin_floor=0.15, snr_rate=0.04)


# --- эталонные числа из раздела 6 ------------------------------------------

def test_reference_our():
    net, gross = normalize_unit_price(1160, 0.16, price_includes_vat=True)
    assert net == pytest.approx(1000)
    cost = effective_cost(net, 0.16, Regime.OUR, supplier_vat_payer=True, has_esf=True)
    assert cost == pytest.approx(1000)
    assert raw_price_for_margin(cost, 0.25, OUR) == pytest.approx(1546.6667, abs=1e-3)
    assert recommend(cost, OUR).price == 1550


def test_reference_snr():
    cost = effective_cost(1000, 0.16, Regime.SNR, supplier_vat_payer=True, has_esf=True)
    assert cost == pytest.approx(1160)
    assert raw_price_for_margin(cost, 0.25, SNR) == pytest.approx(1633.80, abs=0.01)
    assert recommend(cost, SNR).price == 1640


def test_reference_vat_leak():
    assert hypothetical_credit(1_000_000, 0.16) == pytest.approx(137_931.03, abs=0.01)


# --- себестоимость ----------------------------------------------------------

def test_our_without_esf_pays_gross():
    assert effective_cost(1000, 0.16, Regime.OUR, supplier_vat_payer=True, has_esf=False) == pytest.approx(1160)


def test_our_supplier_not_vat_payer():
    assert effective_cost(1000, 0.0, Regime.OUR, supplier_vat_payer=False, has_esf=False) == 1000


def test_zero_vat_line():
    net, gross = normalize_unit_price(500, 0.0, price_includes_vat=True)
    assert net == gross == 500
    assert effective_cost(net, 0.0, Regime.OUR, True, True) == 500


def test_net_price_input():
    net, gross = normalize_unit_price(1000, 0.16, price_includes_vat=False)
    assert gross == pytest.approx(1160)


def test_negative_price_rejected():
    with pytest.raises(PricingError):
        normalize_unit_price(-1, 0.16, True)


# --- маржа ------------------------------------------------------------------

def test_margin_roundtrip_our():
    assert margin(1546.6667, 1000, OUR) == pytest.approx(0.25, abs=1e-5)


def test_margin_roundtrip_snr():
    assert margin(1633.80, 1160, SNR) == pytest.approx(0.25, abs=1e-4)


def test_negative_margin():
    assert margin(1000, 1200, SNR) < 0
    assert margin(1160, 1200, OUR) < 0


def test_margin_none_without_price():
    assert margin(0, 100, OUR) is None
    assert margin(None, 100, OUR) is None


# --- граничные случаи цены -------------------------------------------------

def test_zero_cost_rejected():
    with pytest.raises(PricingError):
        raw_price_for_margin(0, 0.25, OUR)


def test_snr_impossible_margin():
    with pytest.raises(PricingError):
        raw_price_for_margin(100, 0.96, SNR)  # t + m ≥ 1 → деление на ноль/отрицательная цена


def test_our_margin_100_rejected():
    with pytest.raises(PricingError):
        raw_price_for_margin(100, 1.0, OUR)


def test_round_up_exact_step_not_bumped():
    assert round_up(1550.0000000001, 10) == 1550
    assert round_up(1550.01, 10) == 1560
    assert round_up(7, 0) == 7


def test_max_markup_caps_price():
    # себестоимость 1000, наценка не больше 20% → цена без НДС ≤ 1200 → с НДС ≤ 1392 → 1390
    rec = recommend(1000, OUR, max_markup_pct=0.20)
    assert rec.capped
    assert rec.price == 1390
    assert rec.price <= rec.cap_price
    assert rec.low <= rec.price


def test_max_markup_not_binding():
    rec = recommend(1000, OUR, max_markup_pct=0.50)
    assert not rec.capped and rec.price == 1550


def test_recommendation_range():
    rec = recommend(1000, OUR)
    # пол 15%: 1000/0.85*1.16 = 1364.7 → 1370
    assert rec.low == 1370
    assert rec.low < rec.price


# --- рост цен ---------------------------------------------------------------

def test_price_change_empty_history():
    c = ch.price_change(100, date(2026, 10, 1), [])
    assert c.ref_cost is None and c.delta_pct is None
    assert ch.severity(c, 0.3, 0.15, 0.03, 0.08) == ch.Severity.NEW


def test_price_change_last_and_median():
    hist = [ch.HistoryPoint(date(2026, 7, 1), 100), ch.HistoryPoint(date(2026, 8, 1), 90),
            ch.HistoryPoint(date(2026, 9, 1), 104), ch.HistoryPoint(date(2026, 5, 1), 50)]
    c = ch.price_change(110, date(2026, 10, 1), hist)
    assert c.ref_cost == 104
    assert c.ref_median3 == 100
    assert c.delta_pct == pytest.approx(6 / 104)
    assert c.monthly_pct == pytest.approx(c.delta_pct, rel=0.05)


def test_severity_thresholds():
    hist = [ch.HistoryPoint(date(2026, 9, 1), 100)]
    d = date(2026, 10, 1)
    assert ch.severity(ch.price_change(102, d, hist), 0.3, 0.15, 0.03, 0.08) == ch.Severity.OK
    assert ch.severity(ch.price_change(103, d, hist), 0.3, 0.15, 0.03, 0.08) == ch.Severity.WARN
    assert ch.severity(ch.price_change(108, d, hist), 0.3, 0.15, 0.03, 0.08) == ch.Severity.CRITICAL
    # маленький рост, но маржа под полом → критично
    assert ch.severity(ch.price_change(101, d, hist), 0.10, 0.15, 0.03, 0.08) == ch.Severity.CRITICAL


def test_ref_cost_zero_no_division():
    c = ch.price_change(100, date(2026, 10, 1), [ch.HistoryPoint(date(2026, 9, 1), 0)])
    assert c.delta_pct is None


def test_monthly_loss_needs_sales():
    assert ch.monthly_loss(50, None) is None
    assert ch.monthly_loss(50, 0) is None
    assert ch.monthly_loss(50, 40) == 2000
    assert ch.monthly_loss(-10, 40) == 0


def test_faster_than_market():
    assert ch.faster_than_market(0.02, 0.011) is True
    assert ch.faster_than_market(0.005, 0.011) is False
    assert ch.faster_than_market(None, 0.011) is None


# --- НДС ----------------------------------------------------------------------

def test_vat_leak_zero_cases():
    assert hypothetical_credit(0, 0.16) == 0
    assert hypothetical_credit(1000, 0) == 0


def test_regime_status_snr_over_limit():
    st = regime_status(Regime.SNR, 3_000_000_000, 43_250_000, 2_595_000_000)
    assert st.share_of_snr_limit > 1
    assert any("переход на ОУР" in n for n in st.notes)


def test_regime_status_no_turnover():
    st = regime_status(Regime.OUR, None, 43_250_000, 2_595_000_000)
    assert st.share_of_vat_threshold is None
    assert any("не введён" in n for n in st.notes)


def test_tax_config_consistent():
    from app.config import tax_numbers
    t = tax_numbers()
    assert t.vat_rate == 0.16
    assert t.vat_threshold_tenge == 43_250_000
    assert t.snr_limit_tenge == 2_595_000_000
    assert t.snr_rate_min == pytest.approx(t.snr_base_rate * 0.5)
    assert t.snr_rate_max == pytest.approx(t.snr_base_rate * 1.5)
