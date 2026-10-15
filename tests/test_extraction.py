from pathlib import Path

import pytest

from app.extraction.checks import check_document
from app.extraction.engines.esf_pdf import parse_tables
from app.extraction.engines.local_ocr import parse_text
from app.extraction.matching import candidates, normalize, pack_size
from app.extraction.pipeline import BadFile, extract, validate_file
from app.extraction.schema import ExtractedDocument, ExtractedLine

DEMO = Path(__file__).resolve().parent.parent / "demo_data" / "invoices"


def doc(lines, **kw):
    return ExtractedDocument(lines=[ExtractedLine(**l) for l in lines], **kw)


# --- арифметические проверки на «испорченных» данных ------------------------

def test_clean_document_passes():
    d = doc([{"name": "a", "qty": 2, "unit_price": 100, "amount": 200, "vat_rate": 0.16, "vat_amount": 27.59},
             {"name": "b", "qty": 1, "unit_price": 50, "amount": 50, "vat_rate": 0.16, "vat_amount": 6.90}],
            total_gross=250, total_vat=34.49, total_net=215.51)
    assert check_document(d) == []


def test_misread_digit_caught_and_confidence_lowered():
    d = doc([{"name": "a", "qty": 21, "unit_price": 6220, "amount": 141120}])
    issues = check_document(d)
    assert [(i.line, i.field) for i in issues] == [(0, "amount")]
    assert d.lines[0].confidence < 1 and "amount" in d.lines[0].uncertain


def test_total_mismatch():
    d = doc([{"name": "a", "qty": 1, "unit_price": 100, "amount": 100}], total_gross=150)
    assert any(i.field == "total_gross" and i.line is None for i in check_document(d))


def test_rounding_tolerated():
    d = doc([{"name": "a", "qty": 3, "unit_price": 33.33, "amount": 100}])
    assert check_document(d) == []


def test_missing_and_negative_values():
    d = doc([{"name": "a", "qty": None, "unit_price": 10, "amount": 10},
             {"name": "b", "qty": 0, "unit_price": -5, "amount": 0}])
    fields = {(i.line, i.field) for i in check_document(d)}
    assert (0, "qty") in fields and (1, "qty") in fields and (1, "unit_price") in fields


def test_vat_amount_mismatch():
    d = doc([{"name": "a", "qty": 1, "unit_price": 116, "amount": 116, "vat_rate": 0.16, "vat_amount": 20}])
    assert any(i.field == "vat_amount" for i in check_document(d))


def test_net_gross_totals():
    d = doc([], total_net=100, total_vat=16, total_gross=120)
    assert any(i.field == "total_gross" for i in check_document(d))


# --- сопоставление товаров -------------------------------------------------

PRODUCTS = {1: "Цемент М400 50 кг", 2: "Цемент М500 25 кг", 3: "Шпаклёвка финишная 20 кг",
            4: "Краска водоэмульсионная 14 кг", 5: "Лампа светодиодная 10 Вт E27"}


def test_normalize_yo_units_latin():
    assert normalize("Шпаклёвка 20КГ") == normalize("шпаклевка 20 кг")
    assert "кг" in normalize("Цемент 50 килограмм")
    # латинские «двойники» в кириллическом слове
    assert normalize("Цемeнт") == normalize("Цемент")  # e — латинская


def test_pack_size():
    assert pack_size("Цемент, меш. 50кг") == (50.0, "кг")
    assert pack_size("Пена 750мл") == (750.0, "мл")
    assert pack_size("Валик") is None


def test_match_synonym_abbreviation():
    best = candidates("Шпатлевка финиш. 20кг", PRODUCTS)[0]
    assert best.product_id == 3


def test_pack_size_disambiguates():
    best = candidates("Цемент 50кг", PRODUCTS)[0]
    assert best.product_id == 1


def test_alias_wins():
    from app.extraction.matching import normalize as n
    best = candidates("Лампа LED 10W", PRODUCTS, aliases={n("Лампа LED 10W"): 5})[0]
    assert best.product_id == 5 and best.via_alias and best.score == 100


def test_empty_catalog():
    assert candidates("что угодно", {}) == []


# --- разбор текста OCR и PDF-таблиц ----------------------------------------

def test_ocr_text_parsing():
    text = """НАКЛАДНАЯ № 5 от 03.10.2026
1 Гвозди 100 мм 12 кг 1 050 12 600
2 Перчатки х/б 100 шт 220 22 000
Итого: 34 600"""
    d = parse_text(text)
    assert d.doc_date == "2026-10-03"
    assert [(l.name, l.qty, l.unit_price, l.amount) for l in d.lines] == [
        ("Гвозди 100 мм", 12, 1050, 12600), ("Перчатки х/б", 100, 220, 22000)]
    assert d.total_gross == 34600
    assert all(l.confidence < 1 for l in d.lines)


def test_esf_table_parsing():
    tables = [[["№", "Наименование товаров", "Ед. изм.", "Кол-во", "Цена за единицу", "Стоимость без НДС",
                "Ставка НДС", "Сумма НДС", "Стоимость с учетом НДС"],
               ["1", "Цемент М400", "мешок", "10", "2 500,00", "25 000,00", "16%", "4 000,00", "29 000,00"],
               ["", "Итого", "", "", "", "25 000,00", "", "4 000,00", "29 000,00"]]]
    d = parse_tables(tables)
    assert len(d.lines) == 1
    ln = d.lines[0]
    assert (ln.qty, ln.unit_price, ln.amount, ln.vat_rate, ln.vat_amount) == (10, 2500, 25000, 0.16, 4000)
    assert d.prices_include_vat is False
    assert check_document(d) == []


def test_esf_unrelated_table_ignored():
    assert parse_tables([[["a", "b"], ["1", "2"]]]).lines == []


# --- невалидный ввод -------------------------------------------------------

def test_not_an_image(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_text("hello")
    with pytest.raises(BadFile, match="не фото"):
        validate_file(p)


def test_empty_file(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"")
    with pytest.raises(BadFile, match="пустой"):
        validate_file(p)


def test_huge_file(tmp_path):
    p = tmp_path / "x.jpg"
    with open(p, "wb") as f:
        f.write(b"\xff\xd8\xff" + b"0" * (16 * 1024 * 1024))
    with pytest.raises(BadFile, match="больше"):
        validate_file(p)


def test_broken_image(tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    with pytest.raises(BadFile, match="повреждено"):
        validate_file(p)


def test_empty_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n%%EOF")
    with pytest.raises(BadFile):
        validate_file(p)


def test_tiny_image(tmp_path):
    from PIL import Image
    p = tmp_path / "x.png"
    Image.new("RGB", (50, 50), "white").save(p)
    with pytest.raises(BadFile, match="маленькое"):
        validate_file(p)


def test_unreadable_photo_without_engines_fails_honestly():
    from app.extraction.engines.base import ExtractionFailed
    from app.extraction.engines.local_ocr import LocalOCREngine
    if LocalOCREngine().available()[0]:
        pytest.skip("локальный OCR установлен — результат зависит от него")
    with pytest.raises(ExtractionFailed, match="не распознан"):
        extract(DEMO / "05_unreadable_blur.jpg")


def test_demo_engine_marks_demo_and_finds_error():
    res = extract(DEMO / "01_optbaza_angle.jpg")
    assert res.engine == "demo"
    assert len(res.issues) == 1 and res.issues[0].field == "amount"
    unsure = [l for l in res.doc.lines if l.uncertain]
    assert len(unsure) == 2


def test_handwritten_total_mismatch_caught():
    res = extract(DEMO / "04_rassrochka_handwritten.jpg")
    assert any(i.field == "total_gross" for i in res.issues)


def test_skew_estimate_on_rotated_page():
    from PIL import Image, ImageDraw
    from app.extraction.preprocess import estimate_skew
    img = Image.new("RGB", (800, 1000), "white")
    d = ImageDraw.Draw(img)
    for y in range(80, 900, 40):
        d.rectangle([60, y, 740, y + 12], fill="black")
    rot = img.rotate(5, expand=True, fillcolor="white")
    assert abs(estimate_skew(rot) + 5) <= 1.5
