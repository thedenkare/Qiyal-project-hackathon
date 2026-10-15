"""Строгая схема результата распознавания. Её же получает VisionLLM как формат ответа."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedLine(BaseModel):
    name: str = Field(description="Наименование товара как в документе")
    qty: float | None = Field(None, description="Количество")
    unit: str | None = Field(None, description="Единица: шт, кг, л, уп, мешок, м и т.п.")
    unit_price: float | None = Field(None, description="Цена за единицу, как напечатано в документе")
    amount: float | None = Field(None, description="Сумма по строке, как напечатано в документе")
    vat_rate: float | None = Field(None, description="Ставка НДС долей: 0.16, 0.05, 0.10 или 0. null если «без НДС»")
    vat_amount: float | None = Field(None, description="Сумма НДС по строке, если есть")
    uncertain: list[str] = Field(default_factory=list,
                                 description="Поля, которые прочитаны неуверенно: name/qty/unit_price/amount/vat_rate")
    confidence: float = Field(1.0, description="Уверенность 0..1 по строке")


class ExtractedDocument(BaseModel):
    supplier_name: str | None = None
    supplier_bin: str | None = Field(None, description="БИН/ИИН поставщика, 12 цифр")
    doc_number: str | None = None
    doc_date: str | None = Field(None, description="Дата документа в формате ГГГГ-ММ-ДД")
    prices_include_vat: bool = Field(True, description="true если цены и суммы в строках указаны с НДС")
    lines: list[ExtractedLine] = Field(default_factory=list)
    total_net: float | None = Field(None, description="Итого без НДС")
    total_vat: float | None = Field(None, description="Итого НДС")
    total_gross: float | None = Field(None, description="Итого к оплате с НДС")
