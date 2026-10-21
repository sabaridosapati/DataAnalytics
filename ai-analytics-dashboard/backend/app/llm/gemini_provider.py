import asyncio
import json
import math

from google import genai
from google.genai import types

from app.llm.provider import LLMError, LLMQuotaError


def _request_error(exc: Exception, operation: str = "request") -> LLMError:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "")
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return LLMQuotaError("Gemini API quota is exhausted or rate-limited. Ask the administrator to check API limits and billing.")
    return LLMError(f"Gemini {operation} failed: {exc}")


def _normalize(vec: list[float]) -> list[float]:
    # Gemini only returns unit-length vectors at the full 3072 dims; truncated (MRL) vectors need re-normalizing.
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class GeminiProvider:
    def __init__(self, settings):
        # http_options timeout is in milliseconds.
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_s * 1000)),
        )
        self.chat_model = settings.gemini_chat_model
        self.embed_model = settings.gemini_embed_model
        self.embed_dim = settings.embed_dim
        self.temperature = settings.gemini_temperature

    def _config(self, system: str, **extra) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(system_instruction=system, temperature=self.temperature, **extra)

    async def chat_json(self, *, purpose: str, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        try:
            resp = await self.client.aio.models.generate_content(
                model=model or self.chat_model,
                contents=user,
                config=self._config(system, response_mime_type="application/json", response_json_schema=schema),
            )
        except Exception as exc:  # noqa: BLE001
            raise _request_error(exc) from exc
        if not resp.text:
            raise LLMError("The model returned an empty response")
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned invalid JSON") from exc

    async def chat_text(self, *, purpose: str, system: str, user: str, model: str | None = None) -> str:
        try:
            resp = await self.client.aio.models.generate_content(
                model=model or self.chat_model, contents=user, config=self._config(system)
            )
        except Exception as exc:  # noqa: BLE001
            raise _request_error(exc) from exc
        return (resp.text or "").strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 100):
                batch = texts[i : i + 100]
                out.extend(await self._embed_batch(batch))
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _request_error(exc, "embedding request") from exc
        return out

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        config = types.EmbedContentConfig(output_dimensionality=self.embed_dim)
        # gemini-embedding-2 merges a plain list of strings into ONE embedding, so give each text its own Content.
        contents = [types.Content(parts=[types.Part(text=t)]) for t in batch]
        resp = await self.client.aio.models.embed_content(model=self.embed_model, contents=contents, config=config)
        if len(resp.embeddings) == len(batch):
            return [_normalize(list(e.values)) for e in resp.embeddings]
        # Still aggregated: fall back to one request per text (slower, but always correct).
        sem = asyncio.Semaphore(8)

        async def one(text: str) -> list[float]:
            async with sem:
                r = await self.client.aio.models.embed_content(model=self.embed_model, contents=text, config=config)
            if len(r.embeddings) != 1:
                raise LLMError(f"Gemini returned {len(r.embeddings)} embeddings for 1 text")
            return _normalize(list(r.embeddings[0].values))

        return list(await asyncio.gather(*(one(t) for t in batch)))
