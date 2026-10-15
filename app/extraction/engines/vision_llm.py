"""VisionLLMEngine: мультимодальная модель по ключу из .env.

Сейчас реализован провайдер Anthropic (Claude). Ответ — строго JSON по схеме ExtractedDocument
(structured outputs). Модель только читает документ; деньги считает детерминированный код.
Нет ключа → движок недоступен, это не ошибка.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

from ..schema import ExtractedDocument
from .base import Engine, EngineUnavailable, ExtractionFailed

PROMPT = """Это накладная поставщика (Казахстан): товарная накладная, счёт-фактура, ЭСФ или рукописная накладная.
Текст может быть на русском или казахском. Перепиши данные документа в схему.

Правила:
- Переписывай числа ровно как напечатаны. Ничего не пересчитывай и не исправляй, даже если суммы не сходятся.
- unit_price и amount — как в документе; prices_include_vat=true, если цены в строках указаны с НДС.
- vat_rate долей (0.16, 0.12, 0.05, 0.10, 0). «Без НДС» → 0.
- Если символ не читается (мятая бумага, тень, исправление от руки) — дай лучшую версию,
  добавь имя поля в uncertain и снизь confidence строки.
- Не выдумывай строки и итоги, которых нет в документе: отсутствующее → null.
- Дата в формате ГГГГ-ММ-ДД."""


class VisionLLMEngine(Engine):
    name = "vision_llm"
    label = "Claude Vision"
    quality_note = "Распознано мультимодальной моделью. Проверьте подсвеченные ячейки."

    def __init__(self) -> None:
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.model = os.environ.get("MARGINGUARD_LLM_MODEL", "claude-opus-5-5")

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Не задан ANTHROPIC_API_KEY в .env"
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "Не установлен пакет anthropic"
        return True, ""

    def _content_block(self, path: Path) -> dict:
        data = path.read_bytes()
        if data[:4] == b"%PDF":
            return {"type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.standard_b64encode(data).decode()}}
        from ..preprocess import prepare_for_model
        img = prepare_for_model(path)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(buf.getvalue()).decode()}}

    def extract(self, path: Path) -> ExtractedDocument:
        ok, reason = self.available()
        if not ok:
            raise EngineUnavailable(reason)
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key, timeout=120, max_retries=1)
        try:
            resp = client.messages.parse(
                model=self.model,
                max_tokens=16000,
                output_config={"effort": "low"},
                messages=[{"role": "user", "content": [self._content_block(path), {"type": "text", "text": PROMPT}]}],
                output_format=ExtractedDocument,
            )
        except anthropic.APIConnectionError as e:
            raise ExtractionFailed("Нет связи с сервисом распознавания") from e
        except anthropic.RateLimitError as e:
            raise ExtractionFailed("Сервис распознавания перегружен, попробуйте через минуту") from e
        except anthropic.APIStatusError as e:
            raise ExtractionFailed(f"Сервис распознавания вернул ошибку {e.status_code}") from e
        if resp.stop_reason == "refusal":
            raise ExtractionFailed("Модель отказалась обрабатывать документ")
        if resp.parsed_output is None:
            raise ExtractionFailed("Модель вернула ответ не по схеме")
        return resp.parsed_output
