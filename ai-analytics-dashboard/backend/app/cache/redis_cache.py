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
