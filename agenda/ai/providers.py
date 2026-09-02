"""Interfaces de IA e implementações (SPEC §69, §70, §115).

Tudo passa por ``AIProvider`` / ``SpeechProvider`` / ``VisionProvider`` para
evitar lock-in: trocar de fornecedor é trocar a implementação, não o produto.
Nenhum prompt vive fora de ``agenda/ai``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from agenda import config


@dataclass
class AIResult:
    data: dict | list | None = None
    text: str = ""
    model: str = ""
    provider: str = ""
    input_units: int = 0
    output_units: int = 0
    error: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error == "" and (self.data is not None or bool(self.text))


class AIProvider(Protocol):
    name: str

    def structured(self, prompt: str, schema: dict, *, model: str = "") -> AIResult: ...


class SpeechProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, mime_type: str) -> AIResult: ...


class VisionProvider(Protocol):
    name: str

    def read_image(self, image: bytes, mime_type: str, prompt: str, schema: dict) -> AIResult: ...


# --------------------------------------------------------------------------- #
# Provedor nulo — o produto continua funcionando sem chave de IA
# --------------------------------------------------------------------------- #
class NullProvider:
    name = "none"

    def structured(self, prompt: str, schema: dict, *, model: str = "") -> AIResult:
        return AIResult(error="sem provedor de IA configurado", provider=self.name)

    def transcribe(self, audio: bytes, mime_type: str) -> AIResult:
        return AIResult(error="sem provedor de transcrição configurado", provider=self.name)

    def read_image(self, image: bytes, mime_type: str, prompt: str, schema: dict) -> AIResult:
        return AIResult(error="sem provedor de visão configurado", provider=self.name)


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #
class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client = None

    def _client_or_none(self):
        if self._client is None:
            from google import genai  # import tardio: dependência opcional

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def structured(self, prompt: str, schema: dict, *, model: str = "") -> AIResult:
        model = model or config.AI_MODEL_FAST
        try:
            from google.genai import types

            client = self._client_or_none()
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
            data = parse_json(response.text or "")
            usage = getattr(response, "usage_metadata", None)
            return AIResult(
                data=data,
                text=response.text or "",
                model=model,
                provider=self.name,
                input_units=getattr(usage, "prompt_token_count", 0) or 0,
                output_units=getattr(usage, "candidates_token_count", 0) or 0,
            )
        except Exception as exc:  # noqa: BLE001 - falha de IA nunca derruba o app
            return AIResult(error=str(exc), model=model, provider=self.name)

    def transcribe(self, audio: bytes, mime_type: str) -> AIResult:
        try:
            from google.genai import types

            client = self._client_or_none()
            response = client.models.generate_content(
                model=config.SPEECH_MODEL,
                contents=[
                    types.Part.from_bytes(data=audio, mime_type=mime_type or "audio/ogg"),
                    "Transcreva este áudio em português do Brasil. Devolva apenas a transcrição.",
                ],
            )
            return AIResult(
                text=(response.text or "").strip(),
                model=config.SPEECH_MODEL,
                provider=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            return AIResult(error=str(exc), provider=self.name)

    def read_image(self, image: bytes, mime_type: str, prompt: str, schema: dict) -> AIResult:
        try:
            from google.genai import types

            client = self._client_or_none()
            response = client.models.generate_content(
                model=config.AI_MODEL_VISION,
                contents=[
                    types.Part.from_bytes(data=image, mime_type=mime_type or "image/jpeg"),
                    prompt,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
            return AIResult(
                data=parse_json(response.text or ""),
                text=response.text or "",
                model=config.AI_MODEL_VISION,
                provider=self.name,
            )
        except Exception as exc:  # noqa: BLE001
            return AIResult(error=str(exc), provider=self.name)


def parse_json(raw: str):
    """Extrai JSON de uma resposta, tolerando cercas de código."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


# --------------------------------------------------------------------------- #
# Fábrica
# --------------------------------------------------------------------------- #
_text_provider: object | None = None


def get_provider():
    global _text_provider
    if _text_provider is None:
        if config.AI_PROVIDER == "gemini" and config.GEMINI_API_KEY:
            _text_provider = GeminiProvider(config.GEMINI_API_KEY)
        else:
            _text_provider = NullProvider()
    return _text_provider


def get_speech_provider():
    return get_provider()


def get_vision_provider():
    return get_provider()


def ai_available() -> bool:
    return not isinstance(get_provider(), NullProvider)


def record_usage(db, *, user_id, operation, result: AIResult, audio_seconds=0.0, image_pages=0):
    """Registra custo por operação (SPEC §112)."""
    from agenda.models import AiUsage

    # Preço aproximado por 1M tokens (ajuste conforme contrato do fornecedor).
    price_in, price_out = 0.30, 2.50
    cost = (result.input_units / 1_000_000) * price_in + (result.output_units / 1_000_000) * price_out
    db.add(
        AiUsage(
            user_id=user_id,
            operation=operation,
            provider=result.provider,
            model=result.model,
            input_units=result.input_units,
            output_units=result.output_units,
            audio_seconds=audio_seconds,
            image_pages=image_pages,
            estimated_cost=round(cost, 6),
        )
    )
