from __future__ import annotations

from pathlib import Path

from ..schema import ExtractedDocument


class EngineUnavailable(RuntimeError):
    """Движок не настроен (нет ключа, не установлен OCR). Это не ошибка приложения."""


class ExtractionFailed(RuntimeError):
    """Движок запущен, но документ не прочитан."""


class Engine:
    name: str = "base"
    label: str = "База"
    quality_note: str = ""

    def available(self) -> tuple[bool, str]:
        return True, ""

    def extract(self, path: Path) -> ExtractedDocument:  # pragma: no cover
        raise NotImplementedError
