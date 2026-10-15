"""Пайплайн: проверка файла → выбор движка → схема → арифметические проверки.

Никакой тихой подмены: если пришлось взять запасной движок, в результате есть баннер с причиной.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import app_settings
from .checks import Issue, check_document
from .engines.base import Engine, EngineUnavailable, ExtractionFailed
from .engines.demo_golden import DemoGoldenEngine
from .engines.esf_pdf import EsfPdfEngine
from .engines.local_ocr import LocalOCREngine
from .engines.vision_llm import VisionLLMEngine
from .preprocess import BadImage, open_image
from .schema import ExtractedDocument


class BadFile(ValueError):
    """Файл не подходит: не изображение/PDF, пустой, огромный, битый."""


@dataclass
class ExtractionResult:
    doc: ExtractedDocument
    engine: str
    engine_label: str
    quality_note: str
    banners: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def sniff(data: bytes) -> str | None:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] in (b"RIFF",) and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    return None


def validate_file(path: Path) -> str:
    max_mb = app_settings()["extraction"]["max_upload_mb"]
    size = path.stat().st_size
    if size == 0:
        raise BadFile("Файл пустой.")
    if size > max_mb * 1024 * 1024:
        raise BadFile(f"Файл больше {max_mb} МБ. Сожмите фото или снимите заново.")
    kind = sniff(path.read_bytes()[:16])
    if kind is None:
        raise BadFile("Это не фото и не PDF. Поддерживаются JPG, PNG, WEBP и PDF.")
    if kind == "heic":
        raise BadFile("Формат HEIC (iPhone) пока не поддерживается. В настройках камеры выберите «Наиболее совместимый» или пришлите скриншот.")
    if kind == "pdf":
        import pdfplumber
        try:
            with pdfplumber.open(path) as pdf:
                if not pdf.pages:
                    raise BadFile("PDF без страниц.")
        except BadFile:
            raise
        except Exception as e:
            raise BadFile("PDF повреждён или защищён паролем.") from e
    else:
        try:
            img = open_image(path)
        except BadImage as e:
            raise BadFile("Изображение повреждено и не открывается.") from e
        if min(img.size) < 200:
            raise BadFile("Слишком маленькое изображение: сфотографируйте накладную крупнее.")
    return kind


def engines_status() -> list[dict]:
    out = []
    for e in (VisionLLMEngine(), LocalOCREngine()):
        ok, reason = e.available()
        out.append({"name": e.name, "label": e.label, "available": ok, "reason": reason})
    return out


def extract(path: Path, prefer: str = "auto") -> ExtractionResult:
    """prefer: auto | vision_llm | local_ocr. Демо-файлы всегда идут через эталон и помечаются."""
    kind = validate_file(path)
    banners: list[str] = []

    chain: list[Engine] = []
    demo = DemoGoldenEngine()
    if demo.knows(path):
        chain.append(demo)
    elif kind == "pdf" and EsfPdfEngine.has_text(path):
        chain.append(EsfPdfEngine())
    llm, ocr = VisionLLMEngine(), LocalOCREngine()
    chain += [ocr, llm] if prefer == "local_ocr" else [llm, ocr]
    if kind == "pdf":
        chain = [e for e in chain if e.name != "local_ocr"]  # локальный OCR работает только с картинками

    errors: list[str] = []
    for i, engine in enumerate(chain):
        try:
            doc = engine.extract(path)
        except EngineUnavailable as e:
            errors.append(f"{engine.label}: недоступен ({e})")
            continue
        except ExtractionFailed as e:
            errors.append(f"{engine.label}: {e}")
            continue
        if errors:
            banners.append("Использован запасной движок «" + engine.label + "». Причина: " + "; ".join(errors))
        if engine.name == "local_ocr":
            banners.append(engine.quality_note)
        tol = app_settings()["extraction"]
        issues = check_document(doc, tol["amount_tolerance_abs"], tol["amount_tolerance_rel"])
        return ExtractionResult(doc, engine.name, engine.label, engine.quality_note, banners, issues)

    raise ExtractionFailed("Документ не распознан. " + "; ".join(errors) +
                           ". Можно ввести строки вручную или настроить движок (см. README).")
