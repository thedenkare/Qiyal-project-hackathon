"""Сквозной smoke: демо-накладная → проверка → подтверждение → алерты → экспорт. Плюс ошибки ввода."""
import re

import pytest
from fastapi.testclient import TestClient

from app.web.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/settings/reset-demo")
        yield c


def test_pages_open(client):
    for url in ["/", "/changes", "/suppliers", "/regime", "/export", "/settings", "/example", "/export/print"]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "ДЕМО" in r.text.upper(), url


def test_end_to_end_demo(client):
    r = client.post("/upload-demo/01_optbaza_angle.jpg", follow_redirects=False)
    assert r.status_code == 303
    doc_url = r.headers["location"]
    page = client.get(doc_url).text
    assert "ДЕМО-эталон" in page and "Проверка сумм: 1" in page
    doc_id = int(doc_url.rsplit("/", 1)[1])

    # собираем форму как браузер и исправляем ошибочную цену
    form = {"supplier_id": re.search(r'<option value="(\d+)" selected>', page).group(1),
            "doc_date": "2026-10-09", "doc_number": "1187", "prices_include_vat": "1",
            "total_gross": "1233150", "total_vat": "170089.66", "total_net": ""}
    for i in range(9):
        for f in ("name", "qty", "unit", "price", "amount", "vat", "product"):
            m = re.search(rf'name="{f}_{i}"[^>]*?value="([^"]*)"', page) if f not in ("vat", "product") else None
            if m:
                form[f"{f}_{i}"] = m.group(1)
        form[f"vat_{i}"] = "0.16"
        sel = re.search(rf'name="product_{i}".*?<option value="(\d+)" selected', page, re.S)
        form[f"product_{i}"] = sel.group(1)
        form[f"del_{i}"] = "0"
    assert form["price_2"] == "6220"
    chk = client.post(f"/documents/{doc_id}/check", data=form).text
    assert "Проверка сумм: 1" in chk
    form["price_2"] = "6720"
    assert "Суммы сходятся" in client.post(f"/documents/{doc_id}/check", data=form).text

    r = client.post(f"/documents/{doc_id}/confirm", data=form, follow_redirects=False)
    assert r.status_code == 303 and "/changes?doc=" in r.headers["location"]
    ch = client.get(r.headers["location"]).text
    assert "7 позиций" in ch
    assert "критично" in ch

    ex = client.get("/export").text
    assert ex.count("поднять") >= 2
    csv = client.get("/export.csv")
    assert csv.status_code == 200 and "Цемент М400 50 кг" in csv.content.decode("utf-8-sig")

    # применяем новые цены: маржа возвращается, «поднять» исчезает у цемента
    m = re.search(r'name="price_(\d+)" value="(\d+)"', ex)
    r = client.post("/export/apply", data={f"apply_{m.group(1)}": "1", f"price_{m.group(1)}": m.group(2)},
                    follow_redirects=False)
    assert "applied=1" in r.headers["location"]


def test_confirm_requires_supplier(client):
    r = client.post("/upload-demo/02_bazar_clean.png", follow_redirects=False)
    doc_id = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post(f"/documents/{doc_id}/confirm", data={"supplier_id": "", "name_0": "x", "qty_0": "1",
                                                           "price_0": "1", "amount_0": "1", "vat_0": "0",
                                                           "product_0": "new"})
    assert r.status_code == 422 and "Выберите поставщика" in r.text


def test_bad_uploads_do_not_crash(client):
    r = client.post("/upload", files={"file": ("x.txt", b"just text", "text/plain")})
    assert r.status_code == 200 and "не фото" in r.text
    r = client.post("/upload", files={"file": ("x.jpg", b"", "image/jpeg")})
    assert r.status_code == 200
    r = client.post("/upload", data={})
    assert r.status_code == 200 and "Выберите" in r.text


def test_unreadable_photo_goes_to_manual_entry(client):
    r = client.post("/upload-demo/05_unreadable_blur.jpg", follow_redirects=False)
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "Ручной ввод" in page and "не распознан" in page


def test_regime_switch_recalculates(client):
    client.post("/settings", data={"shop_name": "Т", "regime": "snr", "snr_rate": "4", "target_margin": "25",
                                   "margin_floor": "15", "rounding_step": "10"})
    assert "упрощённая декларация" in client.get("/suppliers").text
    client.post("/settings", data={"shop_name": "Т", "regime": "our", "snr_rate": "4", "target_margin": "25",
                                   "margin_floor": "15", "rounding_step": "10"})


def test_csv_import_errors_reported(client):
    r = client.post("/settings/import/sales", files={"file": ("s.csv", "month,product,units,revenue\n2026-13x,Нет,1,\n".encode(), "text/csv")})
    assert r.status_code == 200


def test_no_keys_in_repo():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for p in root.rglob("*"):
        if any(part in (".venv", "data", ".git", "__pycache__") for part in p.parts) or not p.is_file():
            continue
        if p.name == ".env":
            continue  # .env в .gitignore
        if p.suffix in {".py", ".json", ".md", ".txt", ".example", ".html", ".sh", ".bat", ".csv"}:
            assert not re.search(r"sk-ant-[A-Za-z0-9_-]{10,}", p.read_text(errors="ignore")), p
