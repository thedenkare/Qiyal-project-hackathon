"""Загрузка ДЕМО-ДАННЫХ и импорт CSV (каталог, продажи)."""
from __future__ import annotations

import csv
import io
import json
from datetime import date

from sqlmodel import Session, SQLModel, select

from .config import DEMO_DIR, tax_numbers
from .db import (BusinessProfile, Document, DocumentLine, Product, ProductAlias, SalesImport, Supplier,
                 get_engine)
from .domain.pricing import normalize_unit_price
from .extraction.matching import normalize
from .services import SEED_ENGINE


def wipe(s: Session) -> None:
    eng = get_engine()
    s.close()
    SQLModel.metadata.drop_all(eng)
    SQLModel.metadata.create_all(eng)


def _num(v) -> float | None:
    if v is None:
        return None
    v = str(v).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def import_catalog(s: Session, text: str) -> tuple[int, list[str]]:
    """CSV: name, unit, category, shelf_price, monthly_units, max_markup_pct (заголовки обязательны)."""
    errors, n = [], 0
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";" if text.count(";") > text.count(",") else ",")
    if not reader.fieldnames or "name" not in [f.strip().lower() for f in reader.fieldnames]:
        return 0, ["Нет колонки name. Ожидаются: name, unit, category, shelf_price, monthly_units, max_markup_pct"]
    existing = {normalize(p.name): p for p in s.exec(select(Product))}
    for i, row in enumerate(reader, 2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if not row.get("name"):
            errors.append(f"Строка {i}: пустое название")
            continue
        p = existing.get(normalize(row["name"])) or Product(name=row["name"])
        p.unit = row.get("unit") or p.unit
        p.category = row.get("category") or p.category
        if _num(row.get("shelf_price")) is not None:
            p.shelf_price = _num(row.get("shelf_price"))
        if _num(row.get("monthly_units")) is not None:
            p.monthly_units = _num(row.get("monthly_units"))
        mm = _num(row.get("max_markup_pct"))
        p.max_markup_pct = mm / 100 if mm is not None and mm > 1 else mm
        s.add(p)
        n += 1
    s.commit()
    return n, errors


def import_sales(s: Session, text: str) -> tuple[int, list[str]]:
    """CSV: month (ГГГГ-ММ), product, units, revenue. Неизвестные товары пропускаются с ошибкой."""
    errors, n = [], 0
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";" if text.count(";") > text.count(",") else ",")
    products = {normalize(p.name): p for p in s.exec(select(Product))}
    for i, row in enumerate(reader, 2):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        month = row.get("month", "")
        if len(month) != 7 or month[4] != "-":
            errors.append(f"Строка {i}: месяц должен быть в формате ГГГГ-ММ")
            continue
        p = products.get(normalize(row.get("product", "")))
        units, revenue = _num(row.get("units")) or 0, _num(row.get("revenue"))
        if revenue is None:
            if p and p.shelf_price:
                revenue = units * p.shelf_price
            else:
                errors.append(f"Строка {i}: нет выручки и цены товара")
                continue
        s.add(SalesImport(product_id=p.id if p else None, month=month, units=units, revenue=revenue))
        n += 1
        if not p:
            errors.append(f"Строка {i}: товар «{row.get('product')}» не найден в каталоге (выручка учтена в обороте)")
    s.commit()
    return n, errors


def load_demo(s: Session) -> None:
    wipe(s)
    t = tax_numbers()
    s.add(BusinessProfile(id=1, shop_name="Хозтовары (ДЕМО)", regime="our", target_margin=0.25, margin_floor=0.15,
                          snr_rate=t.snr_base_rate, sale_vat_rate=t.vat_rate, is_demo=True))
    sups = json.loads((DEMO_DIR / "suppliers.json").read_text(encoding="utf-8"))
    by_bin = {}
    for sp in sups:
        row = Supplier(name=sp["name"], bin=sp["bin"], vat_payer=sp["vat_payer"], gives_esf=sp["gives_esf"],
                       note=sp["note"])
        s.add(row)
        s.commit()
        s.refresh(row)
        by_bin[sp["bin"]] = row
    import_catalog(s, (DEMO_DIR / "catalog.csv").read_text(encoding="utf-8"))
    products = {p.name: p for p in s.exec(select(Product))}
    hist = json.loads((DEMO_DIR / "history.json").read_text(encoding="utf-8"))
    for a in hist["aliases"]:
        s.add(ProductAlias(supplier_id=by_bin[a["supplier_bin"]].id, alias_norm=normalize(a["alias"]),
                           product_id=products[a["product"]].id))
    s.commit()
    aliases = {(a.supplier_id, a.alias_norm): a.product_id for a in s.exec(select(ProductAlias))}
    for d in hist["documents"]:
        sup = by_bin[d["supplier_bin"]]
        doc = Document(filename=f"история-{d['doc_number']}.pdf", engine=SEED_ENGINE, engine_label="Демо-история",
                       status="confirmed", doc_date=date.fromisoformat(d["doc_date"]), doc_number=d["doc_number"],
                       supplier_id=sup.id, prices_include_vat=True, total_net=d.get("total_net"),
                       total_vat=d.get("total_vat"), total_gross=d["total_gross"], is_demo=True)
        s.add(doc)
        s.commit()
        s.refresh(doc)
        for i, ln in enumerate(d["lines"]):
            net, gross = normalize_unit_price(ln["unit_price"], ln["vat_rate"], True)
            s.add(DocumentLine(document_id=doc.id, idx=i, name=ln["name"], qty=ln["qty"], unit=ln["unit"],
                               unit_price=ln["unit_price"], amount=ln["amount"], vat_rate=ln["vat_rate"],
                               vat_amount=ln.get("vat_amount"), product_id=aliases[(sup.id, normalize(ln["name"]))],
                               unit_net=net, unit_gross=gross))
    s.commit()
    import_sales(s, (DEMO_DIR / "sales.csv").read_text(encoding="utf-8"))
