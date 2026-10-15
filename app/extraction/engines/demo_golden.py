"""DemoGoldenEngine: для наших демо-файлов отдаёт заранее записанный эталон. В UI помечен «ДЕМО».

Файл узнаётся по SHA-256 содержимого, поэтому переименование не мешает, а любой чужой файл
этим движком не распознаётся.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ...config import DEMO_DIR
from ..schema import ExtractedDocument
from .base import Engine, EngineUnavailable

GOLDEN_DIR = DEMO_DIR / "golden"


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DemoGoldenEngine(Engine):
    name = "demo"
    label = "ДЕМО-эталон"
    quality_note = "Демо-файл: данные взяты из заранее подготовленного эталона, а не распознаны."

    def _manifest(self) -> dict:
        m = GOLDEN_DIR / "manifest.json"
        return json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}

    def knows(self, path: Path) -> bool:
        return file_hash(path) in self._manifest()

    def extract(self, path: Path) -> ExtractedDocument:
        entry = self._manifest().get(file_hash(path))
        if not entry:
            raise EngineUnavailable("Файл не является демо-файлом")
        data = json.loads((GOLDEN_DIR / entry).read_text(encoding="utf-8"))
        return ExtractedDocument.model_validate(data)
