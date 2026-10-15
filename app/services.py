"""Склейка домена, распознавания и БД. Все деньги считает детерминированный код здесь и в app/domain."""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from rapidfuzz import fuzz
from sqlmodel import Session, delete, select

from .config import app_settings, tax_numbers
from .db import (Alert, BusinessProfile, Document, DocumentLine, Product, ProductAlias, SalesImport, ShelfPriceChange,
                 Supplier)
from .domain import changes as ch
from .domain.pricing import (PricingError, Profile, Regime, credit_available, effective_cost, margin,
                             normalize_unit_price, recommend)
from .domain.vat import hypothetical_credit, regime_status
from .extraction.checks import check_document
from .extraction.matching import candidates, normalize
from .extraction.pipeline import ExtractionResult
from .extraction.schema import ExtractedDocument, ExtractedLine

SEED_ENGINE = "seed"


# --- профиль ----------------------------------------------------------------

def get_profile_row(s: Session) -> BusinessProfile:
    row = s.get(BusinessProfile, 1)
    if row is None:
        row = BusinessProfile(id=1, snr_rate=tax_numbers().snr_base_rate, sale_vat_rate=tax_numbers().vat_rate)
        s.add(row)
        s.commit()
        s.refresh(row)
    return row


def to_profile(row: BusinessProfile) -> Profile:
    return Profile(regime=Regime(row.regime), target_margin=row.target_margin, margin_floor=row.margin_floor,
                   snr_rate=row.snr_rate, sale_vat_rate=row.sale_vat_rate, rounding_step=row.rounding_step)


def get_profile(s: Session) -> Profile:
    return to_profile(get_profile_row(s))


# --- поставщики -------------------------------------------------------------

def guess_supplier(s: Session, name: str | None, bin_: str | None) -> Supplier | None:
    sups = s.exec(select(Supplier)).all()
    if bin_:
        for sp in sups:
            if sp.bin and sp.bin == bin_.strip():
                return sp
    if name:
        best = max(sups, key=lambda sp: fuzz.token_set_ratio(normalize(name), normalize(sp.name)), default=None)
        if best and fuzz.token_set_ratio(normalize(name), normalize(best.name)) >= 85:
            return best
    return None


# --- сохранение распознанного документа -----------------------------------

def store_extraction(s: Session, res: ExtractionResult, filename: str, stored_path: str) -> Document:
    d = res.doc
    sup = guess_supplier(s, d.supplier_name, d.supplier_bin)
    doc = Document(filename=filename, stored_path=stored_path, engine=res.engine, engine_label=res.engine_label,
                   quality_note=res.quality_note, banners=json.dumps(res.banners, ensure_ascii=False),
                   issues=json.dumps([i.__dict__ for i in res.issues], ensure_ascii=False),
                   doc_date=_parse_date(d.doc_date), doc_number=d.doc_number,
                   supplier_id=sup.id if sup else None, supplier_name_raw=d.supplier_name,
                   supplier_bin_raw=d.supplier_bin, prices_include_vat=d.prices_include_vat,
                   total_net=d.total_net, total_vat=d.total_vat, total_gross=d.total_gross,
                   is_demo=res.engine == "demo")
    s.add(doc)
    s.commit()
    s.refresh(doc)
    for i, ln in enumerate(d.lines):
        s.add(_line_row(doc.id, i, ln))
    s.commit()
    suggest_products(s, doc)
    return doc


def blank_document(s: Session, filename: str, stored_path: str, reason: str) -> Document:
    """Ручной ввод, когда ни один движок не справился. Документ пустой, но путь не обрывается."""
    doc = Document(filename=filename, stored_path=stored_path, engine="manual", engine_label="Ручной ввод",
                   quality_note="Документ не распознан, строки вводятся вручную.",
                   banners=json.dumps([reason], ensure_ascii=False), doc_date=date.today())
    s.add(doc)
    s.commit()
    s.refresh(doc)
    for i in range(3):
        s.add(DocumentLine(document_id=doc.id, idx=i, name="", confidence=0.0))
    s.commit()
    return doc


def _parse_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _line_row(doc_id: int, i: int, ln: ExtractedLine) -> DocumentLine:
    return DocumentLine(document_id=doc_id, idx=i, name=ln.name, qty=ln.qty, unit=ln.unit, unit_price=ln.unit_price,
                        amount=ln.amount, vat_rate=ln.vat_rate or 0.0, vat_amount=ln.vat_amount,
                        confidence=ln.confidence, uncertain=json.dumps(ln.uncertain, ensure_ascii=False))


def product_names(s: Session) -> dict[int, str]:
    return {p.id: p.name for p in s.exec(select(Product)).all()}


def alias_map(s: Session, supplier_id: int | None) -> dict[str, int]:
    rows = s.exec(select(ProductAlias)).all()
    # алиас этого поставщика важнее общего
    out = {a.alias_norm: a.product_id for a in rows if a.supplier_id is None}
    out.update({a.alias_norm: a.product_id for a in rows if a.supplier_id == supplier_id})
    return out


def suggest_products(s: Session, doc: Document) -> None:
    auto = app_settings()["matching"]["auto_accept_score"]
    products = product_names(s)
    aliases = alias_map(s, doc.supplier_id)
    for ln in lines_of(s, doc.id):
        if not ln.name:
            continue
        cands = candidates(ln.name, products, aliases)
        if cands and (cands[0].via_alias or cands[0].score >= auto):
            ln.product_id = cands[0].product_id
            ln.match_score = cands[0].score
            s.add(ln)
    s.commit()


def lines_of(s: Session, doc_id: int) -> list[DocumentLine]:
    return list(s.exec(select(DocumentLine).where(DocumentLine.document_id == doc_id).order_by(DocumentLine.idx)))


def line_candidates(s: Session, doc: Document) -> dict[int, list]:
    products = product_names(s)
    aliases = alias_map(s, doc.supplier_id)
    return {ln.id: candidates(ln.name, products, aliases, limit=3) if ln.name else [] for ln in lines_of(s, doc.id)}


# --- подтверждение ----------------------------------------------------------

@dataclass
class LineInput:
    name: str
    qty: float | None
    unit: str | None
    unit_price: float | None
    amount: float | None
    vat_rate: float
    product_id: int | None       # None — не сопоставлено
    create_product: bool = False
    delete: bool = False


class ConfirmError(ValueError):
    pass


def recheck(doc: Document, inputs: list[LineInput]) -> list:
    ed = ExtractedDocument(prices_include_vat=doc.prices_include_vat, total_net=doc.total_net,
                           total_vat=doc.total_vat, total_gross=doc.total_gross,
                           lines=[ExtractedLine(name=i.name or "?", qty=i.qty, unit=i.unit, unit_price=i.unit_price,
                                                amount=i.amount, vat_rate=i.vat_rate) for i in inputs])
    tol = app_settings()["extraction"]
    return check_document(ed, tol["amount_tolerance_abs"], tol["amount_tolerance_rel"])


def confirm_document(s: Session, doc: Document, supplier_id: int | None, doc_date: date | None,
                     inputs: list[LineInput]) -> Document:
    if supplier_id is None or s.get(Supplier, supplier_id) is None:
        raise ConfirmError("Выберите поставщика: без него нельзя понять, есть ли НДС-зачёт.")
    inputs = [i for i in inputs if not i.delete and (i.name or i.qty or i.unit_price)]
    if not inputs:
        raise ConfirmError("В документе нет ни одной строки.")
    for n, i in enumerate(inputs, 1):
        if not i.name:
            raise ConfirmError(f"Строка {n}: нет названия товара.")
        if i.qty is None or i.qty <= 0 or i.unit_price is None or i.unit_price <= 0:
            raise ConfirmError(f"Строка {n} «{i.name}»: нужны количество и цена больше нуля.")
        if i.product_id is None and not i.create_product:
            raise ConfirmError(f"Строка {n} «{i.name}»: выберите товар из каталога или «Новый товар».")
    doc.supplier_id = supplier_id
    doc.doc_date = doc_date or doc.doc_date or date.today()
    for ln in lines_of(s, doc.id):
        s.delete(ln)
    for n, i in enumerate(inputs):
        pid = i.product_id
        if i.create_product:
            p = Product(name=i.name, unit=i.unit or "шт")
            s.add(p)
            s.commit()
            s.refresh(p)
            pid = p.id
        net, gross = normalize_unit_price(i.unit_price, i.vat_rate, doc.prices_include_vat)
        s.add(DocumentLine(document_id=doc.id, idx=n, name=i.name, qty=i.qty, unit=i.unit, unit_price=i.unit_price,
                           amount=i.amount, vat_rate=i.vat_rate, product_id=pid, unit_net=net, unit_gross=gross,
                           confidence=1.0))
        an = normalize(i.name)
        exists = s.exec(select(ProductAlias).where(ProductAlias.alias_norm == an,
                                                   ProductAlias.supplier_id == supplier_id)).first()
        if exists:
            exists.product_id = pid
            s.add(exists)
        else:
            s.add(ProductAlias(supplier_id=supplier_id, alias_norm=an, product_id=pid))
    doc.status = "confirmed"
    doc.confirmed_at = datetime.now()
    s.add(doc)
    s.commit()
    build_alerts(s, doc)
    return doc


# --- анализ -----------------------------------------------------------------

def line_cost(ln: DocumentLine, sup: Supplier | None, prof: Profile) -> float:
    return effective_cost(ln.unit_net, ln.vat_rate, prof.regime, bool(sup and sup.vat_payer), bool(sup and sup.gives_esf))


def purchase_history(s: Session, product_id: int, supplier_id: int | None, before: date,
                     exclude_doc: int | None, prof: Profile) -> list[ch.HistoryPoint]:
    q = (select(DocumentLine, Document).join(Document, Document.id == DocumentLine.document_id)
         .where(DocumentLine.product_id == product_id, Document.status == "confirmed", Document.doc_date <= before))
    if supplier_id is not None:
        q = q.where(Document.supplier_id == supplier_id)
    pts = []
    for ln, d in s.exec(q):
        if d.id == exclude_doc or ln.unit_net is None:
            continue
        pts.append(ch.HistoryPoint(d.doc_date, line_cost(ln, s.get(Supplier, d.supplier_id), prof)))
    return pts


def build_alerts(s: Session, doc: Document) -> list[Alert]:
    s.exec(delete(Alert).where(Alert.document_id == doc.id))
    if doc.engine == SEED_ENGINE:
        s.commit()
        return []
    prof = get_profile(s)
    cfg = app_settings()["alerts"]
    sup = s.get(Supplier, doc.supplier_id)
    out = []
    for ln in lines_of(s, doc.id):
        if ln.product_id is None or ln.unit_net is None:
            continue
        p = s.get(Product, ln.product_id)
        cost = line_cost(ln, sup, prof)
        hist = purchase_history(s, p.id, doc.supplier_id, doc.doc_date, doc.id, prof)
        c = ch.price_change(cost, doc.doc_date, hist)
        m_before = margin(p.shelf_price, c.ref_cost, prof) if c.ref_cost else None
        m_after = margin(p.shelf_price, cost, prof)
        try:
            rec = recommend(cost, prof, p.max_markup_pct)
        except PricingError:
            rec = None
        sev = ch.severity(c, m_after, prof.margin_floor, cfg["warn_increase_pct"], cfg["critical_increase_pct"])
        loss = ch.monthly_loss(cost - c.ref_cost, p.monthly_units) if c.ref_cost else None
        a = Alert(document_id=doc.id, product_id=p.id, supplier_id=doc.supplier_id, severity=sev.value,
                  delta_pct=c.delta_pct, ref_cost=c.ref_cost, new_cost=cost, margin_before=m_before,
                  margin_after=m_after, rec_price=rec.price if rec else None, rec_low=rec.low if rec else None,
                  rec_capped=rec.capped if rec else False, monthly_loss=loss, monthly_pct=c.monthly_pct)
        s.add(a)
        out.append(a)
    s.commit()
    return out


def rebuild_all_alerts(s: Session) -> None:
    """После смены профиля (режим, маржа) пересчитать всё."""
    for d in s.exec(select(Document).where(Document.status == "confirmed", Document.engine != SEED_ENGINE)):
        build_alerts(s, d)


SEV_ORDER = {"critical": 0, "warn": 1, "new": 2, "ok": 3}


@dataclass
class ChangesView:
    rows: list[dict]
    increased: int
    avg_increase: float | None
    avg_monthly: float | None
    period_months: float | None
    margin_before: float | None
    margin_after: float | None
    loss_total: float | None
    loss_known_for: int
    loss_unknown_for: int
    benchmark: float
    docs: list[Document] = field(default_factory=list)


def changes_view(s: Session, doc_id: int | None = None) -> ChangesView:
    q = select(Alert, Document).join(Document, Document.id == Alert.document_id)
    if doc_id:
        q = q.where(Alert.document_id == doc_id)
    pairs = list(s.exec(q))
    prof = get_profile(s)
    rows = []
    for a, d in pairs:
        p = s.get(Product, a.product_id)
        sup = s.get(Supplier, a.supplier_id) if a.supplier_id else None
        rows.append({"alert": a, "doc": d, "product": p, "supplier": sup,
                     "text": explain(a, p, prof)})
    rows.sort(key=lambda r: (SEV_ORDER.get(r["alert"].severity, 9), -(r["alert"].delta_pct or 0)))
    inc = [r["alert"] for r in rows if (r["alert"].delta_pct or 0) > 0.001]
    avg_inc = statistics.mean(a.delta_pct for a in inc) if inc else None
    monthly = [a.monthly_pct for a in inc if a.monthly_pct is not None]
    months = []
    for r in rows:
        a, d = r["alert"], r["doc"]
        if a.ref_cost is not None and a.monthly_pct is not None and a.delta_pct:
            # период = log(1+Δ)/log(1+Δ/мес)
            import math
            if a.monthly_pct > 0:
                months.append(math.log(1 + a.delta_pct) / math.log(1 + a.monthly_pct))
    mb = [a.margin_before for a in inc if a.margin_before is not None]
    ma = [a.margin_after for a in inc if a.margin_after is not None and a.margin_before is not None]
    losses = [a.monthly_loss for a in inc if a.monthly_loss is not None]
    docs = sorted({d.id: d for _, d in pairs}.values(), key=lambda d: d.doc_date or date.min, reverse=True)
    return ChangesView(rows=rows, increased=len(inc), avg_increase=avg_inc,
                       avg_monthly=statistics.mean(monthly) if monthly else None,
                       period_months=statistics.median(months) if months else None,
                       margin_before=statistics.mean(mb) if mb else None,
                       margin_after=statistics.mean(ma) if ma else None,
                       loss_total=sum(losses) if losses else None, loss_known_for=len(losses),
                       loss_unknown_for=len(inc) - len(losses), benchmark=tax_numbers().nonfood_mom, docs=docs)


def explain(a: Alert, p: Product, prof: Profile) -> str:
    """Объяснение простым языком по шаблону. Все числа уже посчитаны кодом."""
    def pct(x):
        return f"{x * 100:.1f}%".replace(".", ",")

    def tg(x):
        return f"{x:,.0f} ₸".replace(",", " ")

    parts = []
    if a.delta_pct is None:
        parts.append("Первая закупка у этого поставщика — сравнивать не с чем.")
    elif a.delta_pct > 0.001:
        parts.append(f"Себестоимость выросла на {pct(a.delta_pct)}: {tg(a.ref_cost)} → {tg(a.new_cost)} за {p.unit}.")
    elif a.delta_pct < -0.001:
        parts.append(f"Себестоимость снизилась на {pct(-a.delta_pct)}.")
    else:
        parts.append("Цена закупки не изменилась.")
    if p.shelf_price and a.margin_after is not None:
        if a.margin_before is not None and abs(a.margin_before - a.margin_after) > 0.001:
            parts.append(f"При текущей цене на полке {tg(p.shelf_price)} маржа {pct(a.margin_before)} → "
                         f"{pct(a.margin_after)}.")
        else:
            parts.append(f"Маржа при текущей цене {tg(p.shelf_price)}: {pct(a.margin_after)}.")
    elif not p.shelf_price:
        parts.append("Цена на полке не задана — маржу посчитать нельзя.")
    if a.rec_price and p.shelf_price and a.rec_price > p.shelf_price:
        parts.append(f"Чтобы вернуть целевую маржу {pct(prof.target_margin)}, цена ≈ {tg(a.rec_price)} "
                     f"(минимум под пол маржи {tg(a.rec_low)}).")
    if a.rec_capped:
        parts.append("Рекомендация ограничена максимальной наценкой для этого товара.")
    if a.monthly_loss:
        parts.append(f"Если цену не менять, это ≈ {tg(a.monthly_loss)} маржи в месяц при {p.monthly_units:.0f} "
                     f"{p.unit}/мес.")
    elif a.delta_pct and a.delta_pct > 0.001 and not p.monthly_units:
        parts.append("Введите продажи в месяц, чтобы оценить потери в ₸.")
    return " ".join(parts)


# --- поставщики -------------------------------------------------------------

@dataclass
class SupplierRow:
    supplier: Supplier
    purchases_gross: float
    purchases_months: int
    credit: bool
    leak: float
    monthly_growth: float | None
    faster: bool | None
    products: int


def suppliers_view(s: Session, months: int = 6) -> tuple[list[SupplierRow], dict]:
    prof = get_profile(s)
    nonfood = tax_numbers().nonfood_mom
    since = date.today() - timedelta(days=int(months * 30.4))
    rows: list[SupplierRow] = []
    chart: dict = {"labels": [], "series": []}
    all_months: set[str] = set()
    per_sup_month: dict[int, dict[str, list[float]]] = {}
    for sup in s.exec(select(Supplier).order_by(Supplier.name)):
        q = (select(DocumentLine, Document).join(Document, Document.id == DocumentLine.document_id)
             .where(Document.supplier_id == sup.id, Document.status == "confirmed"))
        pairs = list(s.exec(q))
        recent = [(ln, d) for ln, d in pairs if d.doc_date and d.doc_date >= since]
        gross = sum((ln.unit_gross or 0) * (ln.qty or 0) for ln, _ in recent)
        has_credit = credit_available(prof.regime, 0.16, sup.vat_payer, sup.gives_esf)
        no_credit_gross = 0.0 if prof.regime == Regime.SNR else sum(
            (ln.unit_gross or 0) * (ln.qty or 0) for ln, _ in recent
            if not credit_available(prof.regime, ln.vat_rate or 0.16, sup.vat_payer, sup.gives_esf))
        leak = hypothetical_credit(no_credit_gross, prof.sale_vat_rate)
        # рост: по каждому товару первая и последняя закупка, средний месячный рост с весом по сумме
        by_prod: dict[int, list[tuple[date, float, float]]] = defaultdict(list)
        for ln, d in pairs:
            if ln.product_id and ln.unit_net:
                by_prod[ln.product_id].append((d.doc_date, line_cost(ln, sup, prof), (ln.unit_gross or 0) * (ln.qty or 0)))
        growths, weights = [], []
        idx_month: dict[str, list[float]] = defaultdict(list)
        for pts in by_prod.values():
            pts.sort()
            first = pts[0]
            for dd, cost, _ in pts:
                key = dd.strftime("%Y-%m")
                idx_month[key].append(cost / first[1] * 100)
                all_months.add(key)
            if len(pts) >= 2:
                m = ch.months_between(first[0], pts[-1][0])
                if m >= 0.5:
                    growths.append((pts[-1][1] / first[1]) ** (1 / m) - 1)
                    weights.append(sum(w for *_, w in pts) or 1)
        mg = sum(g * w for g, w in zip(growths, weights)) / sum(weights) if growths else None
        per_sup_month[sup.id] = idx_month
        rows.append(SupplierRow(sup, gross, months, has_credit, leak, mg, ch.faster_than_market(mg, nonfood),
                                len(by_prod)))
    labels = sorted(all_months)
    chart["labels"] = labels
    for r in rows:
        series = per_sup_month[r.supplier.id]
        chart["series"].append({"label": r.supplier.name,
                                "data": [round(statistics.mean(series[m]), 2) if m in series else None for m in labels]})
    if labels:
        chart["benchmark"] = [round(100 * (1 + nonfood) ** i, 2) for i in range(len(labels))]
    return rows, chart


# --- режим и НДС ------------------------------------------------------------

def turnover_12m(s: Session) -> tuple[float | None, str]:
    row = get_profile_row(s)
    if row.manual_turnover_12m:
        return row.manual_turnover_12m, "введено вручную в настройках"
    months = sorted({x.month for x in s.exec(select(SalesImport))})
    if not months:
        return None, ""
    last12 = months[-12:]
    total = sum(x.revenue for x in s.exec(select(SalesImport)) if x.month in last12)
    return total, f"из импорта продаж, {last12[0]} — {last12[-1]} ({len(last12)} мес.)"


def regime_view(s: Session) -> dict:
    t = tax_numbers()
    prof = get_profile(s)
    turnover, src = turnover_12m(s)
    st = regime_status(prof.regime, turnover, t.vat_threshold_tenge, t.snr_limit_tenge)
    sup_rows, _ = suppliers_view(s, months=12)
    total = sum(r.purchases_gross for r in sup_rows)
    with_credit = sum(r.purchases_gross for r in sup_rows if r.credit)
    leak = sum(r.leak for r in sup_rows)
    return {"status": st, "turnover_source": src, "tax": t, "purchases_12m": total,
            "share_with_credit": with_credit / total if total else None, "leak_12m": leak, "profile": prof}


# --- экспорт цен ------------------------------------------------------------

def latest_costs(s: Session, prof: Profile) -> dict[int, dict]:
    q = (select(DocumentLine, Document).join(Document, Document.id == DocumentLine.document_id)
         .where(Document.status == "confirmed", DocumentLine.product_id.is_not(None)))
    out: dict[int, dict] = {}
    for ln, d in s.exec(q):
        if ln.unit_net is None:
            continue
        cur = out.get(ln.product_id)
        if cur is None or (d.doc_date, d.id) > (cur["date"], cur["doc_id"]):
            sup = s.get(Supplier, d.supplier_id)
            out[ln.product_id] = {"date": d.doc_date, "doc_id": d.id, "cost": line_cost(ln, sup, prof),
                                  "supplier": sup.name if sup else "—"}
    return out


def export_rows(s: Session) -> list[dict]:
    prof = get_profile(s)
    costs = latest_costs(s, prof)
    rows = []
    for p in s.exec(select(Product).order_by(Product.category, Product.name)):
        c = costs.get(p.id)
        if not c:
            rows.append({"product": p, "cost": None, "margin": None, "rec": None, "action": "no_cost"})
            continue
        m = margin(p.shelf_price, c["cost"], prof)
        try:
            rec = recommend(c["cost"], prof, p.max_markup_pct)
        except PricingError:
            rec = None
        action = "keep"
        if not p.shelf_price:
            action = "set"
        elif rec and rec.price > p.shelf_price and (m is None or m < prof.target_margin - 0.01):
            action = "raise"
        rows.append({"product": p, "cost": c, "margin": m, "rec": rec, "action": action})
    order = {"raise": 0, "set": 1, "keep": 2, "no_cost": 3}
    rows.sort(key=lambda r: (order[r["action"]], r["product"].category, r["product"].name))
    return rows


def apply_prices(s: Session, prices: dict[int, float]) -> int:
    n = 0
    for pid, price in prices.items():
        p = s.get(Product, pid)
        if p is None or price is None or price <= 0 or price == p.shelf_price:
            continue
        s.add(ShelfPriceChange(product_id=pid, old_price=p.shelf_price, new_price=price))
        p.shelf_price = price
        s.add(p)
        n += 1
    s.commit()
    if n:
        rebuild_all_alerts(s)
    return n
