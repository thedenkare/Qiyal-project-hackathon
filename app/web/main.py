"""Веб-приложение MarginGuard: FastAPI + Jinja2 + HTMX. Мобильный-first, русский интерфейс."""
from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from .. import services as sv
from ..config import DATA_DIR, DEMO_DIR, app_settings, source, tax_numbers, tax_params
from ..db import Alert, Document, DocumentLine, Product, SalesImport, Supplier, session
from ..extraction.engines.base import ExtractionFailed
from ..extraction.pipeline import BadFile, engines_status, extract
from ..seed import import_catalog, import_sales, load_demo

log = logging.getLogger("marginguard")
HERE = Path(__file__).resolve().parent
UPLOADS = DATA_DIR / "uploads"
DEMO_INVOICES = DEMO_DIR / "invoices"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with session() as s:
        # Первый запуск: сразу загружаем демо, чтобы приложение не было пустым
        if s.exec(select(Supplier)).first() is None and s.exec(select(Product)).first() is None:
            load_demo(s)
    yield


app = FastAPI(title="MarginGuard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# --- форматирование -----------------------------------------------------------

def f_tg(v, digits: int = 0) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}".replace(",", " ")
    return s.replace(".", ",") + " ₸"


def f_num(v) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def f_pct(v, digits: int = 1, sign: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{v * 100:+.{digits}f}" if sign else f"{v * 100:.{digits}f}"
    return s.replace(".", ",").replace("-", "−") + "%"


def f_grp(v) -> str:
    return "—" if v is None else f"{v:,.0f}".replace(",", "\u202f")


def f_date(d) -> str:
    return d.strftime("%d.%m.%Y") if d else "—"


templates.env.filters.update(tg=f_tg, pct=f_pct, num=f_num, ddate=f_date, grp=f_grp)
templates.env.globals.update(src=source)


def render(request: Request, name: str, ctx: dict | None = None, status: int = 200) -> HTMLResponse:
    with session() as s:
        prof = sv.get_profile_row(s)
        base = {"profile": prof, "is_demo": prof.is_demo, "nav": name.split(".")[0]}
    base.update(ctx or {})
    return templates.TemplateResponse(request, name, base, status_code=status)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)  # без содержимого документов
    return render(request, "error.html", {"title": "Что-то пошло не так",
                                          "message": "Внутренняя ошибка. Данные не потеряны. Вернитесь назад и попробуйте ещё раз."},
                  status=500)


# --- загрузка -----------------------------------------------------------------

def demo_files() -> list[dict]:
    names = {
        "01_optbaza_angle.jpg": "Оптбаза, фото под углом (главный сценарий)",
        "02_bazar_clean.png": "Базар-Строй, ровный скан, без НДС",
        "03_hozryad_shadow.jpg": "Хозряд, тень и складки",
        "04_rassrochka_handwritten.jpg": "Рассрочка, исправление от руки",
        "05_unreadable_blur.jpg": "Размытое фото (покажет ошибку)",
    }
    return [{"file": k, "title": v} for k, v in names.items() if (DEMO_INVOICES / k).exists()]


@app.get("/", response_class=HTMLResponse)
def upload_page(request: Request, error: str | None = None):
    with session() as s:
        docs = s.exec(select(Document).where(Document.engine != sv.SEED_ENGINE)
                      .order_by(Document.created_at.desc()).limit(10)).all()
        sups = {x.id: x.name for x in s.exec(select(Supplier))}
    return render(request, "upload.html", {"docs": docs, "sups": sups, "demo_files": demo_files(),
                                           "engines": engines_status(), "error": error})


def _process(path: Path, filename: str, prefer: str = "auto"):
    try:
        res = extract(path, prefer=prefer)
    except BadFile as e:
        path.unlink(missing_ok=True)
        return None, str(e)
    with session() as s:
        if res is None:
            return None, "Не удалось распознать"
        doc = sv.store_extraction(s, res, filename, str(path))
        return doc.id, None


def _process_or_manual(path: Path, filename: str, prefer: str):
    try:
        return _process(path, filename, prefer)
    except ExtractionFailed as e:
        with session() as s:
            doc = sv.blank_document(s, filename, str(path), str(e))
            return doc.id, None


@app.post("/upload")
async def upload(request: Request, file: UploadFile = File(None), engine: str = Form("auto")):
    if file is None or not file.filename:
        return upload_page(request, error="Выберите фото или PDF накладной.")
    max_bytes = app_settings()["extraction"]["max_upload_mb"] * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        return upload_page(request, error=f"Файл больше {app_settings()['extraction']['max_upload_mb']} МБ.")
    ext = Path(file.filename).suffix.lower()[:6] or ".bin"
    path = UPLOADS / f"{uuid.uuid4().hex}{ext}"
    path.write_bytes(data)
    doc_id, err = _process_or_manual(path, file.filename, engine)
    if err:
        return upload_page(request, error=err)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@app.post("/upload-demo/{name}")
def upload_demo(request: Request, name: str):
    src = DEMO_INVOICES / Path(name).name
    if not src.exists():
        return upload_page(request, error="Демо-файл не найден.")
    path = UPLOADS / f"{uuid.uuid4().hex}{src.suffix}"
    path.write_bytes(src.read_bytes())
    doc_id, err = _process_or_manual(path, src.name, "auto")
    if err:
        return upload_page(request, error=err)
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@app.get("/demo-file/{name}")
def demo_file(name: str):
    p = DEMO_INVOICES / Path(name).name
    if not p.exists():
        return Response(status_code=404)
    return FileResponse(p)


# --- проверка документа -------------------------------------------------------

def _review_ctx(s, doc: Document, error: str | None = None, issues=None) -> dict:
    lines = sv.lines_of(s, doc.id)
    cands = sv.line_candidates(s, doc)
    if issues is None:
        issues = json.loads(doc.issues or "[]")
    cell_issue = {(i["line"], i["field"]): i["message"] for i in issues if i["line"] is not None}
    return {"doc": doc, "lines": lines, "cands": cands, "products": s.exec(select(Product).order_by(Product.name)).all(),
            "suppliers": s.exec(select(Supplier).order_by(Supplier.name)).all(),
            "banners": json.loads(doc.banners or "[]"), "issues": issues, "cell_issue": cell_issue,
            "uncertain": {ln.id: set(json.loads(ln.uncertain or "[]")) for ln in lines},
            "low_conf": app_settings()["extraction"]["low_confidence"], "error": error,
            "vat_rates": tax_params()["allowed_vat_rates"], "engines": engines_status()}


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
def review(request: Request, doc_id: int):
    with session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return render(request, "error.html", {"title": "Документ не найден", "message": "Возможно, он удалён."}, 404)
        if doc.status == "confirmed" and doc.engine != sv.SEED_ENGINE:
            return RedirectResponse(f"/changes?doc={doc_id}", status_code=303)
        return render(request, "review.html", _review_ctx(s, doc))


@app.get("/documents/{doc_id}/file")
def doc_file(doc_id: int):
    with session() as s:
        doc = s.get(Document, doc_id)
        if not doc or not doc.stored_path or not Path(doc.stored_path).exists():
            return Response(status_code=404)
        return FileResponse(doc.stored_path)


def _f(v: str | None) -> float | None:
    if v is None:
        return None
    v = v.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_lines(form) -> list[sv.LineInput]:
    idxs = sorted({int(k.split("_", 1)[1]) for k in form.keys() if k.startswith("name_")})
    out = []
    for i in idxs:
        prod = form.get(f"product_{i}", "")
        out.append(sv.LineInput(
            name=(form.get(f"name_{i}") or "").strip(), qty=_f(form.get(f"qty_{i}")),
            unit=(form.get(f"unit_{i}") or "").strip() or None, unit_price=_f(form.get(f"price_{i}")),
            amount=_f(form.get(f"amount_{i}")), vat_rate=_f(form.get(f"vat_{i}")) or 0.0,
            product_id=int(prod) if prod.isdigit() else None, create_product=prod == "new",
            delete=form.get(f"del_{i}") == "1"))
    return out


@app.post("/documents/{doc_id}/check", response_class=HTMLResponse)
async def recheck(request: Request, doc_id: int):
    form = await request.form()
    with session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return HTMLResponse("")
        doc.prices_include_vat = form.get("prices_include_vat") == "1"
        doc.total_gross = _f(form.get("total_gross"))
        doc.total_vat = _f(form.get("total_vat"))
        doc.total_net = _f(form.get("total_net"))
        inputs = [i for i in _parse_lines(form) if not i.delete]
        issues = [i.__dict__ for i in sv.recheck(doc, inputs)]
        return templates.TemplateResponse(request, "_checks.html", {"issues": issues, "live": True})


@app.post("/documents/{doc_id}/confirm", response_class=HTMLResponse)
async def confirm(request: Request, doc_id: int):
    form = await request.form()
    with session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return render(request, "error.html", {"title": "Документ не найден", "message": ""}, 404)
        doc.prices_include_vat = form.get("prices_include_vat") == "1"
        doc.doc_number = (form.get("doc_number") or "").strip() or doc.doc_number
        doc.total_gross = _f(form.get("total_gross"))
        doc.total_vat = _f(form.get("total_vat"))
        doc.total_net = _f(form.get("total_net"))
        sup_raw = form.get("supplier_id", "")
        supplier_id = int(sup_raw) if sup_raw.isdigit() else None
        if sup_raw == "new" and (form.get("new_supplier_name") or "").strip():
            sp = Supplier(name=form.get("new_supplier_name").strip(), bin=(form.get("new_supplier_bin") or "").strip() or None,
                          vat_payer=form.get("new_supplier_vat") == "1", gives_esf=form.get("new_supplier_esf") == "1")
            s.add(sp)
            s.commit()
            s.refresh(sp)
            supplier_id = sp.id
        try:
            d = date.fromisoformat(form.get("doc_date") or "")
        except ValueError:
            d = None
        inputs = _parse_lines(form)
        try:
            sv.confirm_document(s, doc, supplier_id, d, inputs)
        except sv.ConfirmError as e:
            s.rollback()
            doc = s.get(Document, doc_id)
            ctx = _review_ctx(s, doc, error=str(e))
            ctx["form_lines"] = inputs
            return render(request, "review.html", ctx, status=422)
    return RedirectResponse(f"/changes?doc={doc_id}", status_code=303)


@app.post("/documents/{doc_id}/delete")
def delete_doc(doc_id: int):
    with session() as s:
        doc = s.get(Document, doc_id)
        if doc and doc.engine != sv.SEED_ENGINE:
            for a in s.exec(select(Alert).where(Alert.document_id == doc_id)):
                s.delete(a)
            for ln in sv.lines_of(s, doc_id):
                s.delete(ln)
            if doc.stored_path:
                Path(doc.stored_path).unlink(missing_ok=True)
            s.delete(doc)
            s.commit()
    return RedirectResponse("/", status_code=303)


# --- что изменилось -----------------------------------------------------------

@app.get("/changes", response_class=HTMLResponse)
def changes(request: Request, doc: int | None = None):
    with session() as s:
        view = sv.changes_view(s, doc)
        current = s.get(Document, doc) if doc else None
        sup = s.get(Supplier, current.supplier_id) if current and current.supplier_id else None
        return render(request, "changes.html", {"v": view, "current": current, "current_sup": sup,
                                                "p": sv.get_profile(s)})


# --- поставщики ---------------------------------------------------------------

@app.get("/suppliers", response_class=HTMLResponse)
def suppliers(request: Request):
    with session() as s:
        rows, chart = sv.suppliers_view(s)
        return render(request, "suppliers.html", {"rows": rows, "chart": json.dumps(chart, ensure_ascii=False),
                                                  "p": sv.get_profile(s), "bench": tax_numbers().nonfood_mom,
                                                  "leak_total": sum(r.leak for r in rows)})


@app.post("/suppliers/{sid}")
async def supplier_update(request: Request, sid: int):
    form = await request.form()
    with session() as s:
        sp = s.get(Supplier, sid)
        if sp:
            sp.vat_payer = form.get("vat_payer") == "1"
            sp.gives_esf = form.get("gives_esf") == "1" and sp.vat_payer
            s.add(sp)
            s.commit()
            sv.rebuild_all_alerts(s)
    return RedirectResponse("/suppliers", status_code=303)


# --- режим и НДС --------------------------------------------------------------

@app.get("/regime", response_class=HTMLResponse)
def regime(request: Request):
    with session() as s:
        return render(request, "regime.html", {"r": sv.regime_view(s), "tp": tax_params()})


# --- экспорт цен --------------------------------------------------------------

@app.get("/export", response_class=HTMLResponse)
def export(request: Request, applied: int | None = None):
    with session() as s:
        return render(request, "export.html", {"rows": sv.export_rows(s), "p": sv.get_profile(s), "applied": applied})


@app.post("/export/apply")
async def export_apply(request: Request):
    form = await request.form()
    prices = {}
    for k in form.keys():
        if k.startswith("apply_") and form.get(k) == "1":
            pid = int(k.split("_")[1])
            prices[pid] = _f(form.get(f"price_{pid}"))
    with session() as s:
        n = sv.apply_prices(s, prices)
    return RedirectResponse(f"/export?applied={n}", status_code=303)


@app.get("/export.csv")
def export_csv():
    with session() as s:
        rows = sv.export_rows(s)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Товар", "Ед.", "Категория", "Цена на полке", "Рекомендуемая цена", "Нижняя граница",
                "Себестоимость", "Маржа сейчас, %", "Действие"])
    labels = {"raise": "поднять", "set": "задать цену", "keep": "оставить", "no_cost": "нет закупок"}
    for r in rows:
        p, rec, c = r["product"], r["rec"], r["cost"]
        w.writerow([p.name, p.unit, p.category, f_num(p.shelf_price), f_num(rec.price) if rec else "",
                    f_num(rec.low) if rec else "", f_num(round(c["cost"], 2)) if c else "",
                    f"{r['margin'] * 100:.1f}".replace(".", ",") if r["margin"] is not None else "", labels[r["action"]]])
    data = "﻿" + buf.getvalue()  # BOM, чтобы Excel открыл кириллицу
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=marginguard_prices.csv"})


@app.get("/export/print", response_class=HTMLResponse)
def export_print(request: Request, only: str = "raise"):
    with session() as s:
        rows = sv.export_rows(s)
        if only == "raise":
            rows = [r for r in rows if r["action"] == "raise"]
        return render(request, "print_prices.html", {"rows": rows, "today": date.today(), "only": only})


@app.get("/example", response_class=HTMLResponse)
def example_onepager(request: Request):
    """Одностраничный «пример результата» для разговоров с предпринимателями (печать на A4)."""
    with session() as s:
        view = sv.changes_view(s)
        rows, _ = sv.suppliers_view(s)
        return render(request, "example.html", {"v": view, "sups": rows, "p": sv.get_profile(s),
                                                "leak_total": sum(r.leak for r in rows)})


# --- настройки ----------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str | None = None, errors: str | None = None):
    with session() as s:
        n_sales = len(s.exec(select(SalesImport)).all())
        products = s.exec(select(Product).order_by(Product.category, Product.name)).all()
        return render(request, "settings.html", {"tp": tax_params(), "tax": tax_numbers(), "msg": msg,
                                                 "errors": json.loads(errors) if errors else [], "n_sales": n_sales,
                                                 "products": products, "engines": engines_status()})


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    t = tax_numbers()
    with session() as s:
        row = sv.get_profile_row(s)
        row.shop_name = (form.get("shop_name") or row.shop_name).strip()
        row.regime = "snr" if form.get("regime") == "snr" else "our"
        snr = _f(form.get("snr_rate"))
        if snr is not None:
            row.snr_rate = min(max(snr / 100, t.snr_rate_min), t.snr_rate_max)
        tm, fl = _f(form.get("target_margin")), _f(form.get("margin_floor"))
        if tm is not None and 0 < tm < 90:
            row.target_margin = tm / 100
        if fl is not None and 0 <= fl < 90:
            row.margin_floor = min(fl / 100, row.target_margin)
        step = _f(form.get("rounding_step"))
        if step is not None and step >= 0:
            row.rounding_step = int(step)
        row.manual_turnover_12m = _f(form.get("manual_turnover_12m"))
        s.add(row)
        s.commit()
        sv.rebuild_all_alerts(s)
    return RedirectResponse("/settings?msg=Сохранено", status_code=303)


@app.post("/settings/products")
async def products_save(request: Request):
    form = await request.form()
    with session() as s:
        for p in s.exec(select(Product)):
            if f"shelf_{p.id}" in form:
                p.shelf_price = _f(form.get(f"shelf_{p.id}"))
                p.monthly_units = _f(form.get(f"units_{p.id}"))
                mm = _f(form.get(f"mm_{p.id}"))
                p.max_markup_pct = mm / 100 if mm is not None else None
                s.add(p)
        s.commit()
        sv.rebuild_all_alerts(s)
    return RedirectResponse("/settings?msg=Каталог+сохранён#catalog", status_code=303)


@app.post("/settings/import/{kind}")
async def import_csv(kind: str, file: UploadFile = File(None)):
    if file is None or not file.filename:
        return RedirectResponse("/settings?msg=Файл+не+выбран", status_code=303)
    raw = await file.read(5 * 1024 * 1024)
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return RedirectResponse("/settings?msg=Не+удалось+прочитать+кодировку+файла", status_code=303)
    with session() as s:
        if kind == "catalog":
            n, errs = import_catalog(s, text)
        elif kind == "sales":
            n, errs = import_sales(s, text)
        else:
            return RedirectResponse("/settings", status_code=303)
        sv.rebuild_all_alerts(s)
    from urllib.parse import quote
    return RedirectResponse(f"/settings?msg={quote(f'Импортировано строк: {n}')}&errors={quote(json.dumps(errs[:20], ensure_ascii=False))}",
                            status_code=303)


@app.post("/settings/reset-demo")
def reset_demo():
    with session() as s:
        load_demo(s)
    for p in UPLOADS.glob("*"):
        p.unlink(missing_ok=True)
    return RedirectResponse("/?", status_code=303)


@app.post("/settings/start-clean")
def start_clean():
    """Начать со своими данными: пустая БД, профиль по умолчанию."""
    from ..seed import wipe
    with session() as s:
        wipe(s)
    with session() as s:
        sv.get_profile_row(s)
        s.add(Supplier(name="Мой поставщик", vat_payer=False))
        s.commit()
    return RedirectResponse("/settings?msg=Данные+очищены.+Импортируйте+каталог", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
