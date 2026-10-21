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
