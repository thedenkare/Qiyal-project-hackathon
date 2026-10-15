"""SQLite через SQLModel. Одна компания, без регистрации. Файл БД лежит локально в data/."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import NaiveDatetime
from sqlmodel import Field, Session, SQLModel, create_engine

from .config import DATA_DIR


class BusinessProfile(SQLModel, table=True):
    id: int | None = Field(default=1, primary_key=True)
    shop_name: str = "Мой магазин"
    regime: str = "our"                # our | snr
    snr_rate: float = 0.04
    target_margin: float = 0.25
    margin_floor: float = 0.15
    rounding_step: int = 10
    sale_vat_rate: float = 0.16
    manual_turnover_12m: float | None = None
    is_demo: bool = False


class Supplier(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    bin: str | None = Field(default=None, index=True)
    vat_payer: bool = False
    gives_esf: bool = False
    note: str = ""


class Product(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    unit: str = "шт"
    category: str = ""
    shelf_price: float | None = None
    monthly_units: float | None = None
    max_markup_pct: float | None = None


class ProductAlias(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    supplier_id: int | None = Field(default=None, index=True)
    alias_norm: str = Field(index=True)
    product_id: int


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str = ""
    stored_path: str = ""
    engine: str = ""
    engine_label: str = ""
    quality_note: str = ""
    banners: str = "[]"                # JSON
    issues: str = "[]"                 # JSON: [{line, field, message}]
    status: str = "draft"              # draft | confirmed
    doc_date: date | None = None
    doc_number: str | None = None
    supplier_id: int | None = None
    supplier_name_raw: str | None = None
    supplier_bin_raw: str | None = None
    prices_include_vat: bool = True
    total_net: float | None = None
    total_vat: float | None = None
    total_gross: float | None = None
    is_demo: bool = False
    created_at: NaiveDatetime = Field(default_factory=datetime.now)
    confirmed_at: NaiveDatetime | None = None


class DocumentLine(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True)
    idx: int = 0
    name: str = ""
    qty: float | None = None
    unit: str | None = None
    unit_price: float | None = None    # как в документе
    amount: float | None = None
    vat_rate: float = 0.0
    vat_amount: float | None = None
    confidence: float = 1.0
    uncertain: str = "[]"              # JSON
    product_id: int | None = Field(default=None, index=True)
    match_score: float | None = None
    unit_net: float | None = None
    unit_gross: float | None = None


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True)
    product_id: int
    supplier_id: int | None = None
    severity: str = "ok"
    delta_pct: float | None = None
    ref_cost: float | None = None
    new_cost: float = 0
    margin_before: float | None = None
    margin_after: float | None = None
    rec_price: float | None = None
    rec_low: float | None = None
    rec_capped: bool = False
    monthly_loss: float | None = None
    monthly_pct: float | None = None
    created_at: NaiveDatetime = Field(default_factory=datetime.now)


class SalesImport(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    product_id: int | None = Field(default=None, index=True)
    month: str = ""                    # YYYY-MM
    units: float = 0
    revenue: float = 0


class ShelfPriceChange(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    product_id: int
    old_price: float | None = None
    new_price: float
    changed_at: NaiveDatetime = Field(default_factory=datetime.now)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DATA_DIR / 'marginguard.db'}",
                                connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    """Для тестов: следующий get_engine() откроет БД по текущему DATA_DIR."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def session() -> Session:
    return Session(get_engine())
