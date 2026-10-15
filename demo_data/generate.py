"""Генератор ДЕМО-ДАННЫХ MarginGuard.

Всё выдумано: магазин «Хозтовары (ДЕМО)», поставщики с пометкой «демо», цены.
Рост цен: ~1–1,5% в месяц (ориентир — непродовольственная инфляция) плюс 3 «выброса» в октябре.

Запуск:  python demo_data/generate.py
Создаёт: catalog.csv, suppliers.json, history.json, sales.csv, invoices/*, golden/*.
Генерация детерминирована (фиксированный seed), повторный запуск даёт те же файлы.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent
rng = random.Random(2026)

SHOP = "Магазин «Хозтовары» (ДЕМО), г. Тараз"
HISTORY_MONTHS = [date(2026, m, 8) for m in (5, 6, 7, 8, 9)]
OCT = date(2026, 10, 9)

SUPPLIERS = [
    {"key": "S1", "name": "ТОО «Демо-Оптбаза»", "bin": "000000000001", "vat_payer": True, "gives_esf": True,
     "note": "Оптовая база, работает с НДС и ЭСФ"},
    {"key": "S2", "name": "ИП «Базар-Строй» (демо)", "bin": "000000000002", "vat_payer": False, "gives_esf": False,
     "note": "Базарный поставщик, без НДС"},
    {"key": "S3", "name": "ИП «Хозряд» (демо)", "bin": "000000000003", "vat_payer": False, "gives_esf": False,
     "note": "Базарный поставщик, без НДС"},
    {"key": "S4", "name": "ТОО «Рассрочка-Демо»", "bin": "000000000004", "vat_payer": True, "gives_esf": True,
     "note": "С НДС, отгружает в рассрочку"},
]

# name (каталог), invoice_name (как пишет поставщик), unit, supplier, May price (как в накладной), %/мес,
# октябрьский выброс, продажи в месяц, категория, max_markup
PRODUCTS = [
    ("Цемент М400 50 кг", "Цемент ПЦ-400 Д20, меш. 50кг", "мешок", "S1", 2900, 0.010, 0.09, 120, "Сухие смеси", None),
    ("Шпаклёвка финишная 20 кг", "Шпатлевка финиш. 20кг", "мешок", "S1", 4200, 0.012, 0.0, 40, "Сухие смеси", None),
    ("Краска водоэмульсионная 14 кг", "Краска в/э интерьерная 14 кг", "шт", "S1", 6300, 0.013, 0.0, 30, "Краски", None),
    ("Грунтовка глубокого проникновения 10 л", "Грунт глуб. проникн. 10л", "шт", "S1", 3100, 0.011, 0.0, 25, "Краски", None),
    ("Клей плиточный 25 кг", "Клей для плитки 25 кг", "мешок", "S1", 2600, 0.008, 0.12, 60, "Сухие смеси", None),
    ("Пена монтажная 750 мл", "Пена монт. проф. 750мл", "шт", "S1", 1850, 0.014, 0.0, 80, "Химия", None),
    ("Саморезы по дереву 3,5×35, 1 кг", "Саморез д/дерева 3,5х35 (1кг)", "уп", "S1", 1700, 0.012, 0.0, 50, "Крепёж", None),
    ("Валик малярный 250 мм", "Валик 250мм", "шт", "S1", 1100, 0.0, 0.0, 25, "Инструмент", None),
    ("Скотч малярный 48 мм", "Лента малярная 48мм", "шт", "S1", 620, 0.0, 0.0, 70, "Инструмент", None),
    ("Гвозди строительные 100 мм, 1 кг", "Гвозди 100 мм", "кг", "S2", 1050, 0.015, 0.0, 60, "Крепёж", None),
    ("Перчатки рабочие х/б", "Перчатки х/б", "шт", "S2", 220, 0.010, 0.0, 200, "Хозтовары", None),
    ("Мешки для мусора 120 л, 10 шт", "Мешки мусорные 120л", "уп", "S2", 750, 0.012, 0.0, 90, "Хозтовары", None),
    ("Ведро пластиковое 12 л", "Ведро 12л", "шт", "S2", 1300, 0.008, 0.0, 20, "Хозтовары", None),
    ("Изолента ПВХ 19 мм", "Изолента синяя", "шт", "S3", 280, 0.010, 0.0, 100, "Электрика", None),
    ("Лампа светодиодная 10 Вт E27", "Лампа LED 10W E27", "шт", "S3", 650, 0.009, 0.14, 120, "Электрика", 0.45),
    ("Удлинитель 3 розетки 5 м", "Удлинитель 3гн 5м", "шт", "S3", 2900, 0.011, 0.0, 15, "Электрика", None),
    ("Кисть малярная 50 мм", "Кисть плоская 50", "шт", "S3", 450, 0.010, 0.0, 40, "Инструмент", None),
    ("Профиль направляющий ПН 27×28, 3 м", "Профиль направляющий 27х28 3м", "шт", "S4", 1150, 0.012, 0.0, 100, "Гипсокартон", None),
    ("Гипсокартон 12,5 мм 1200×2500", "ГКЛ 12,5 мм", "шт", "S4", 3400, 0.010, 0.0, 60, "Гипсокартон", None),
    ("Плинтус ПВХ 2,5 м", "Плинтус ПВХ 2,5м", "шт", "S4", 900, 0.010, 0.0, 50, "Отделка", None),
]

SUP = {s["key"]: s for s in SUPPLIERS}


def r10(x: float) -> int:
    return int(round(x / 10.0)) * 10


def price_at(p, month_idx: int, october: bool = False) -> int:
    base, mom, outlier = p[4], p[5], p[6]
    v = base * (1 + mom) ** month_idx
    if october:
        v *= 1 + outlier
    return r10(v)


def make_lines(sup_key: str, month_idx: int, october: bool) -> list[dict]:
    out = []
    for p in PRODUCTS:
        if p[3] != sup_key:
            continue
        price = price_at(p, month_idx, october)
        qty = max(2, int(round(p[7] * rng.uniform(0.6, 1.1))))
        line = {"name": p[1], "qty": qty, "unit": p[2], "unit_price": price, "amount": price * qty}
        if SUP[sup_key]["vat_payer"]:
            line["vat_rate"] = 0.16
            line["vat_amount"] = round(price * qty * 16 / 116, 2)
        else:
            line["vat_rate"] = 0
            line["vat_amount"] = None
        out.append(line)
    return out


def make_doc(sup_key: str, d: date, number: str, lines: list[dict]) -> dict:
    s = SUP[sup_key]
    gross = sum(ln["amount"] for ln in lines)
    doc = {"supplier_name": s["name"], "supplier_bin": s["bin"], "doc_number": number, "doc_date": d.isoformat(),
           "prices_include_vat": True, "lines": lines, "total_gross": gross}
    if s["vat_payer"]:
        doc["total_vat"] = round(sum(ln["vat_amount"] for ln in lines), 2)
        doc["total_net"] = round(gross - doc["total_vat"], 2)
    return doc


# --- рендер накладной ---------------------------------------------------------

def font(size: int, bold: bool = False, hand: bool = False) -> ImageFont.FreeTypeFont:
    cands = []
    if hand:
        cands += ["/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
                  "/System/Library/Fonts/Noteworthy.ttc", "C:/Windows/Fonts/segoepr.ttf"]
    if bold:
        cands += ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    cands += ["/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for c in cands:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    raise SystemExit("Не найден шрифт с кириллицей (Arial или DejaVuSans)")


def fmt(v: float) -> str:
    s = f"{v:,.2f}".replace(",", " ")
    return s[:-3] if s.endswith(".00") else s.replace(".", ",")


def render(doc: dict, handwritten_fix: dict | None = None) -> Image.Image:
    W, H = 1240, 1754
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    f, fb, fs, fh = font(24), font(30, bold=True), font(18), font(26, bold=True)
    d.text((70, 60), f"НАКЛАДНАЯ № {doc['doc_number']} от {date.fromisoformat(doc['doc_date']).strftime('%d.%m.%Y')}",
           font=fb, fill=(20, 20, 20))
    d.text((70, 120), f"Поставщик: {doc['supplier_name']}   БИН {doc['supplier_bin']}", font=f, fill=(30, 30, 30))
    d.text((70, 160), f"Получатель: {SHOP}", font=f, fill=(30, 30, 30))
    vat = doc.get("total_vat") is not None
    d.text((70, 200), "Цены указаны с НДС 16%" if vat else "Без НДС (поставщик не является плательщиком НДС)",
           font=fs, fill=(70, 70, 70))
    cols = [70, 120, 640, 740, 860, 1020, 1170]
    heads = ["№", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма"]
    y = 260
    d.rectangle([cols[0], y, cols[-1], y + 44], outline=(40, 40, 40), width=2)
    for i, h in enumerate(heads):
        d.text((cols[i] + 8, y + 10), h, font=fh if i else f, fill=(20, 20, 20))
    y += 44
    row_h = 46
    for n, ln in enumerate(doc["lines"], 1):
        d.rectangle([cols[0], y, cols[-1], y + row_h], outline=(90, 90, 90), width=1)
        for x in cols[1:-1]:
            d.line([x, y, x, y + row_h], fill=(90, 90, 90))
        cells = [str(n), ln["name"], ln["unit"], fmt(ln["qty"]), fmt(ln["unit_price"]), fmt(ln["amount"])]
        if handwritten_fix and handwritten_fix["line"] == n - 1:
            cells[4] = fmt(handwritten_fix["old_price"])
            cells[5] = fmt(handwritten_fix["old_amount"])
        for i, c in enumerate(cells):
            tw = d.textlength(c, font=f)
            x = cols[i] + 8 if i in (0, 1, 2) else cols[i + 1] - tw - 10
            d.text((x, y + 11), c, font=f, fill=(25, 25, 25))
        if handwritten_fix and handwritten_fix["line"] == n - 1:
            hf = font(30, hand=True)
            for i, key in ((4, "new_price"), (5, "new_amount")):
                x0, x1 = cols[i] + 10, cols[i + 1] - 10
                d.line([x0, y + 26, x1, y + 20], fill=(30, 50, 160), width=3)
                d.text((x0 + 10, y - 24), fmt(handwritten_fix[key]), font=hf, fill=(30, 50, 160))
        y += row_h
    y += 20
    total_printed = doc["total_gross"] if not handwritten_fix else handwritten_fix["printed_total"]
    d.text((cols[4] - 120, y), "Итого:", font=fh, fill=(20, 20, 20))
    t = fmt(total_printed)
    d.text((cols[-1] - d.textlength(t, font=fh) - 10, y), t, font=fh, fill=(20, 20, 20))
    if vat:
        y += 40
        d.text((cols[4] - 220, y), "в т.ч. НДС 16%:", font=f, fill=(30, 30, 30))
        t = fmt(doc["total_vat"])
        d.text((cols[-1] - d.textlength(t, font=f) - 10, y), t, font=f, fill=(30, 30, 30))
    y += 90
    d.text((70, y), "Отпустил ____________        Получил ____________", font=f, fill=(30, 30, 30))
    d.ellipse([820, y - 40, 1000, y + 120], outline=(60, 80, 170), width=4)
    d.text((850, y + 30), "М.П. (демо)", font=fs, fill=(60, 80, 170))
    d.text((70, H - 60), "ДЕМО-ДАННЫЕ: документ сгенерирован программно, все названия вымышлены.",
           font=fs, fill=(150, 150, 150))
    return img


def effect_angled(img: Image.Image) -> Image.Image:
    rot = img.rotate(7, expand=True, fillcolor=(96, 84, 70), resample=Image.BICUBIC)
    W, H = rot.size
    # лёгкая перспектива: верх сужен
    quad = (int(W * 0.04), 0, 0, H, W, H, int(W * 0.96), 0)
    persp = rot.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=(96, 84, 70))
    bg = Image.new("RGB", (W + 120, H + 120), (96, 84, 70))
    bg.paste(persp, (60, 60))
    return bg.resize((bg.width * 3 // 4, bg.height * 3 // 4))


def effect_shadow(img: Image.Image) -> Image.Image:
    W, H = img.size
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for i in range(W):
        sd.line([i, 0, i, H], fill=int(max(0, 110 - i * 0.12)))
    dark = Image.new("RGB", (W, H), (40, 40, 40))
    out = Image.composite(dark, img, shade)
    d = ImageDraw.Draw(out)
    for _ in range(14):  # «мятые» складки
        x0, y0 = rng.randint(0, W), rng.randint(0, H)
        d.line([x0, y0, x0 + rng.randint(-400, 400), y0 + rng.randint(-200, 200)],
               fill=(205, 203, 198), width=2)
    noise = Image.effect_noise((W, H), 18).convert("RGB")
    out = Image.blend(out, noise, 0.08)
    return out.filter(ImageFilter.GaussianBlur(0.8))


def effect_blurry(img: Image.Image) -> Image.Image:
    small = img.resize((img.width // 3, img.height // 3))
    return small.filter(ImageFilter.GaussianBlur(6)).rotate(-3, expand=True, fillcolor=(80, 80, 80))


def main() -> None:
    (OUT / "invoices").mkdir(exist_ok=True)
    (OUT / "golden").mkdir(exist_ok=True)

    with open(OUT / "catalog.csv", "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["name", "unit", "category", "shelf_price", "monthly_units", "max_markup_pct", "supplier_bin"])
        for p in PRODUCTS:
            s = SUP[p[3]]
            # Цена на полке выставлена в июне и с тех пор не менялась («меняет, когда вспомнит»)
            june = price_at(p, 1)
            cost = june / 1.16 if s["vat_payer"] else june  # магазин на ОУР: зачёт только у S1/S4
            shelf = math.ceil(cost / (1 - 0.28) * 1.16 / 10) * 10
            w.writerow([p[0], p[2], p[8], shelf, p[7], p[9] if p[9] is not None else "", s["bin"]])

    (OUT / "suppliers.json").write_text(json.dumps(SUPPLIERS, ensure_ascii=False, indent=2), encoding="utf-8")

    history = []
    n = 100
    for mi, d in enumerate(HISTORY_MONTHS):
        for s in SUPPLIERS:
            n += 1
            history.append(make_doc(s["key"], d, str(n), make_lines(s["key"], mi, False)))
    # для истории: накладные «по бумаге» + алиасы поставщика, которые уже подтверждены
    hist_out = {"documents": history,
                "aliases": [{"supplier_bin": SUP[p[3]]["bin"], "alias": p[1], "product": p[0]} for p in PRODUCTS]}
    (OUT / "history.json").write_text(json.dumps(hist_out, ensure_ascii=False, indent=1), encoding="utf-8")

    # продажи за 12 месяцев (окт 2025 — сен 2026)
    with open(OUT / "sales.csv", "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["month", "product", "units", "revenue"])
        catalog = {r["name"]: r for r in csv.DictReader(open(OUT / "catalog.csv", encoding="utf-8"))}
        for k in range(12):
            y, m = (2025, 10 + k) if k < 3 else (2026, k - 2)
            season = 1.25 if m in (5, 6, 7, 8, 9) else 0.8  # стройсезон
            for p in PRODUCTS:
                units = max(0, int(round(p[7] * season * rng.uniform(0.85, 1.15))))
                w.writerow([f"{y}-{m:02d}", p[0], units, units * int(catalog[p[0]]["shelf_price"])])

    # --- октябрьские накладные (демо-файлы для загрузки) ---
    manifest = {}
    oct_idx = 5

    def save(img: Image.Image, fname: str, golden: dict | None, **kw):
        path = OUT / "invoices" / fname
        img.save(path, **kw)
        if golden is not None:
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            gname = fname.rsplit(".", 1)[0] + ".json"
            (OUT / "golden" / gname).write_text(json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")
            manifest[h] = gname

    # 1) Главный сценарий: Демо-Оптбаза, фото под углом. 7 позиций подорожали.
    doc_a = make_doc("S1", OCT, "1187", make_lines("S1", oct_idx, True))
    golden_a = json.loads(json.dumps(doc_a))
    # Имитация ошибки распознавания: «6 840» прочитано как «6 340» в цене краски.
    paint = next(ln for ln in golden_a["lines"] if ln["name"].startswith("Краска"))
    true_price = paint["unit_price"]
    paint["unit_price"] = int(str(true_price).replace("8", "3", 1)) if "8" in str(true_price) else true_price - 500
    paint["uncertain"] = ["unit_price"]
    paint["confidence"] = 0.7
    golden_a["lines"][6]["uncertain"] = ["qty"]  # неуверенно прочитанное, но верное количество
    golden_a["lines"][6]["confidence"] = 0.72
    golden_a["_demo_note"] = f"Эталон с намеренной ошибкой OCR: цена краски {true_price} записана как {paint['unit_price']}"
    save(effect_angled(render(doc_a)), "01_optbaza_angle.jpg", golden_a, quality=82)
    save(render(doc_a), "01_optbaza_clean.png", doc_a)

    # 2) Базар-Строй: чистый скан
    doc_b = make_doc("S2", date(2026, 10, 6), "47", make_lines("S2", oct_idx, True))
    save(render(doc_b), "02_bazar_clean.png", doc_b)

    # 3) Хозряд: тень и складки
    doc_c = make_doc("S3", date(2026, 10, 7), "312", make_lines("S3", oct_idx, True))
    golden_c = json.loads(json.dumps(doc_c))
    golden_c["lines"][0]["uncertain"] = ["name"]
    golden_c["lines"][0]["confidence"] = 0.8
    save(effect_shadow(render(doc_c)), "03_hozryad_shadow.jpg", golden_c, quality=80)

    # 4) Рассрочка-Демо: рукописное исправление цены, итог не исправлен
    doc_d = make_doc("S4", date(2026, 10, 8), "2025", make_lines("S4", oct_idx, True))
    ln = doc_d["lines"][1]
    old_price = ln["unit_price"] - 80
    fix = {"line": 1, "old_price": old_price, "old_amount": old_price * ln["qty"],
           "new_price": ln["unit_price"], "new_amount": ln["amount"],
           "printed_total": doc_d["total_gross"] - (ln["unit_price"] - old_price) * ln["qty"]}
    golden_d = json.loads(json.dumps(doc_d))
    golden_d["total_gross"] = fix["printed_total"]  # печатный итог остался старым
    golden_d["lines"][1]["uncertain"] = ["unit_price", "amount"]
    golden_d["lines"][1]["confidence"] = 0.65
    save(render(doc_d, fix), "04_rassrochka_handwritten.jpg", golden_d, quality=85)

    # 5) Нечитаемое фото: эталона нет — показывает честную ошибку/ручной ввод
    save(effect_blurry(render(doc_b)), "05_unreadable_blur.jpg", None, quality=60)

    (OUT / "golden" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("Готово:", len(history), "исторических накладных,", len(manifest), "демо-эталонов")


if __name__ == "__main__":
    main()
