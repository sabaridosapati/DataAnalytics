# Task 3: LLM provider (OpenAI + Fake) and Redis cache

**Files:**
- Create: `backend/app/llm/__init__.py` (empty), `backend/app/llm/provider.py`, `backend/app/llm/openai_provider.py`, `backend/app/llm/fake_provider.py`, `backend/app/llm/demo_script.py` (placeholder; filled in Task 10)
- Create: `backend/app/cache/__init__.py` (empty), `backend/app/cache/redis_cache.py`
- Test: `backend/tests/unit/test_llm_provider.py`, `backend/tests/unit/test_cache.py`

**Interfaces:**
- Produces `app.llm.provider`: `class LLMError(Exception)`; protocol `LLMProvider` with
  - `async chat_json(*, purpose: str, system: str, user: str, schema: dict, model: str | None = None) -> dict`
  - `async chat_text(*, purpose: str, system: str, user: str, model: str | None = None) -> str`
  - `async embed(texts: list[str]) -> list[list[float]]`
  - `build_provider(settings) -> LLMProvider` (raises `RuntimeError` if `openai` without key).
- Produces `app.llm.fake_provider`: `Rule(purpose, contains, response)`; `FakeLLMProvider(rules, dim)` with `.rules` (mutable list, first match wins) and `.calls: list[tuple[str, str]]`; `fake_embed(text, dim)`. Rule `contains` is matched (case-insensitive) against the text after `User question:` in the user message (up to end of that line), or the whole message if no marker. Callable responses get the full user message.
- **Prompt convention (all agents must follow):** every user prompt sent to the LLM contains a line `User question: <single-line question>`.
- Produces `app.cache.redis_cache`: `RedisCache(url)` with `get_json(key)`, `set_json(key, value, ttl)`, `ping()`, `close()`; `normalize_question(q)`, `sha(text)`, `answer_key(version, question)`, `sql_key(version, sql)`, `emb_key(model, text)`.

- [ ] **Step 1: Write failing tests**

**File: `backend/tests/unit/test_llm_provider.py`**
```python
import math

import pytest

from app.config import Settings
from app.llm.fake_provider import FakeLLMProvider, Rule, fake_embed
from app.llm.provider import LLMError, build_provider


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


async def test_rule_matching_first_wins_and_uses_question_marker():
    llm = FakeLLMProvider(rules=[Rule("intent", "revenue", {"a": 1}), Rule("intent", "", {"a": 2})])
    assert await llm.chat_json(purpose="intent", system="", user="Catalog...\nUser question: Total REVENUE?", schema={}) == {"a": 1}
    # 'revenue' appears only in the catalog part, not the question -> falls through
    assert await llm.chat_json(purpose="intent", system="", user="revenue (double)\nUser question: hello", schema={}) == {"a": 2}


async def test_callable_response_gets_full_message():
    llm = FakeLLMProvider(rules=[Rule("aggregate", "", lambda user: user.upper())])
    assert await llm.chat_text(purpose="aggregate", system="", user="User question: abc") == "USER QUESTION: ABC"


async def test_missing_rule_raises():
    with pytest.raises(LLMError):
        await FakeLLMProvider().chat_json(purpose="sql", system="", user="x", schema={})


async def test_describe_columns_has_default():
    assert await FakeLLMProvider().chat_json(purpose="describe_columns", system="", user="x", schema={}) == {"descriptions": []}


async def test_responses_are_copies():
    rule = Rule("intent", "", {"steps": [1]})
    llm = FakeLLMProvider(rules=[rule])
    out = await llm.chat_json(purpose="intent", system="", user="q", schema={})
    out["steps"].append(2)
    assert rule.response == {"steps": [1]}


async def test_calls_recorded():
    llm = FakeLLMProvider(rules=[Rule("x", "", "ok")])
    await llm.chat_text(purpose="x", system="", user="hello")
    assert llm.calls == [("x", "hello")]


async def test_fake_embeddings_deterministic_and_semantic():
    llm = FakeLLMProvider(dim=1536)
    a, b, c = await llm.embed(["supply chain disruption", "supply chain issues", "employee salary"])
    assert len(a) == 1536
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)
    assert cos(a, b) > cos(a, c)
    assert fake_embed("supply chain disruption", 1536) == a


def test_fake_embed_empty_text_is_not_zero_vector():
    v = fake_embed("", 8)
    assert math.isclose(sum(x * x for x in v), 1.0)


def test_build_provider_requires_key():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_provider(Settings(llm_provider="openai", openai_api_key=""))


def test_build_provider_fake():
    assert isinstance(build_provider(Settings(llm_provider="fake")), FakeLLMProvider)


def test_build_provider_unknown():
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        build_provider(Settings(llm_provider="nope"))
```

**File: `backend/tests/unit/test_cache.py`**
```python
from app.cache.redis_cache import RedisCache, answer_key, emb_key, normalize_question, sql_key


def test_normalize_question():
    assert normalize_question("  Total   Revenue?? ") == "total revenue"
    assert normalize_question("total revenue") == "total revenue"


def test_keys_depend_on_version_and_content():
    assert answer_key(1, "Total revenue?") == answer_key(1, "total   revenue")
    assert answer_key(1, "q") != answer_key(2, "q")
    assert sql_key(1, "SELECT 1") != sql_key(1, "SELECT 2")
    assert emb_key("m", "t").startswith("emb:m:")


async def test_unreachable_redis_degrades_gracefully():
    cache = RedisCache("redis://127.0.0.1:1/0")
    assert await cache.get_json("k") is None
    await cache.set_json("k", {"a": 1}, 10)  # must not raise
    assert await cache.ping() is False
    await cache.close()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit/test_llm_provider.py tests/unit/test_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.llm'`.

- [ ] **Step 3: Implement the provider layer**

**File: `backend/app/llm/__init__.py`**
```python
```

**File: `backend/app/llm/provider.py`**
```python
from typing import Protocol


class LLMError(Exception):
    """The language model could not produce a usable response."""


class LLMProvider(Protocol):
    async def chat_json(self, *, purpose: str, system: str, user: str, schema: dict, model: str | None = None) -> dict: ...

    async def chat_text(self, *, purpose: str, system: str, user: str, model: str | None = None) -> str: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def build_provider(settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        from app.llm.demo_script import DEMO_RULES
        from app.llm.fake_provider import FakeLLMProvider

        return FakeLLMProvider(rules=DEMO_RULES, dim=settings.embed_dim)
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when LLM_PROVIDER=openai (or set LLM_PROVIDER=fake for demo mode)")
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    raise RuntimeError(f"Unknown LLM_PROVIDER '{settings.llm_provider}' (expected 'openai' or 'fake')")
```

**File: `backend/app/llm/openai_provider.py`**
```python
import json

from openai import AsyncOpenAI

from app.llm.provider import LLMError


class OpenAIProvider:
    def __init__(self, settings):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=2, timeout=60)
        self.chat_model = settings.openai_chat_model
        self.embed_model = settings.openai_embed_model
        self.temperature = settings.openai_temperature

    def _params(self, model: str | None) -> dict:
        params: dict = {"model": model or self.chat_model}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params

    async def chat_json(self, *, purpose: str, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        try:
            resp = await self.client.chat.completions.create(
                **self._params(model),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": purpose, "schema": schema, "strict": True},
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        message = resp.choices[0].message
        if getattr(message, "refusal", None):
            raise LLMError(f"The model refused: {message.refusal}")
        if not message.content:
            raise LLMError("The model returned an empty response")
        try:
            return json.loads(message.content)
        except json.JSONDecodeError as exc:
            raise LLMError("The model returned invalid JSON") from exc

    async def chat_text(self, *, purpose: str, system: str, user: str, model: str | None = None) -> str:
        try:
            resp = await self.client.chat.completions.create(
                **self._params(model),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        return (resp.choices[0].message.content or "").strip()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), 100):
                resp = await self.client.embeddings.create(model=self.embed_model, input=texts[i : i + 100])
                out.extend(d.embedding for d in resp.data)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI embedding request failed: {exc}") from exc
        return out
```

**File: `backend/app/llm/fake_provider.py`**
```python
"""Deterministic, offline LLM used by tests and by the free demo mode."""
import copy
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from app.llm.provider import LLMError

QUESTION_RE = re.compile(r"User question:\s*(.*)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9]+")
DEFAULTS: dict[str, Any] = {"describe_columns": {"descriptions": []}}


@dataclass
class Rule:
    purpose: str
    contains: str  # case-insensitive substring of the user question ("" matches everything)
    response: Any  # dict / str, or callable(user_message) -> dict / str


def _match_target(user: str) -> str:
    found = QUESTION_RE.search(user)
    return found.group(1) if found else user


def fake_embed(text: str, dim: int) -> list[float]:
    """Bag-of-words hashing embedding: texts sharing words have higher cosine similarity."""
    v = [0.0] * dim
    for tok in TOKEN_RE.findall(text.lower()):
        h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:8], "big")
        v[h % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        v[0] = 1.0
        return v
    return [x / norm for x in v]


class FakeLLMProvider:
    def __init__(self, rules: list[Rule] | None = None, dim: int = 1536):
        self.rules: list[Rule] = list(rules or [])
        self.dim = dim
        self.calls: list[tuple[str, str]] = []

    def _respond(self, purpose: str, user: str) -> Any:
        self.calls.append((purpose, user))
        target = _match_target(user).lower()
        for rule in self.rules:
            if rule.purpose == purpose and rule.contains.lower() in target:
                response: Callable | Any = rule.response
                result = response(user) if callable(response) else response
                return copy.deepcopy(result)
        if purpose in DEFAULTS:
            return copy.deepcopy(DEFAULTS[purpose])
        raise LLMError(f"FakeLLM has no rule for purpose={purpose!r}")

    async def chat_json(self, *, purpose: str, system: str, user: str, schema: dict, model: str | None = None) -> dict:
        return self._respond(purpose, user)

    async def chat_text(self, *, purpose: str, system: str, user: str, model: str | None = None) -> str:
        return str(self._respond(purpose, user))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [fake_embed(t, self.dim) for t in texts]
```

**File: `backend/app/llm/demo_script.py`**
```python
# Scripted responses for free demo mode. Filled in Task 10.
DEMO_RULES: list = []
```

- [ ] **Step 4: Implement the cache**

**File: `backend/app/cache/__init__.py`**
```python
```

**File: `backend/app/cache/redis_cache.py`**
```python
import hashlib
import json
import logging

import redis.asyncio as redis

log = logging.getLogger(__name__)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_question(question: str) -> str:
    return " ".join(question.lower().split()).rstrip("?.! ")


def answer_key(version: int, question: str) -> str:
    return f"ans:{version}:{sha(normalize_question(question))}"


def sql_key(version: int, sql: str) -> str:
    return f"sql:{version}:{sha(sql)}"


def emb_key(model: str, text: str) -> str:
    return f"emb:{model}:{sha(text)}"


class RedisCache:
    """JSON cache that never breaks a request: failures are logged and treated as misses."""

    def __init__(self, url: str):
        self.r = redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=2)

    async def get_json(self, key: str):
        try:
            value = await self.r.get(key)
            return json.loads(value) if value is not None else None
        except Exception as exc:  # noqa: BLE001
            log.warning("cache get failed for %s: %s", key, exc)
            return None

    async def set_json(self, key: str, value, ttl: int) -> None:
        try:
            await self.r.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache set failed for %s: %s", key, exc)

    async def ping(self) -> bool:
        try:
            return bool(await self.r.ping())
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        try:
            await self.r.aclose()
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm --no-deps backend pytest tests/unit -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend
git commit -m "feat(llm): provider interface, OpenAI + offline fake provider, Redis cache"
```
