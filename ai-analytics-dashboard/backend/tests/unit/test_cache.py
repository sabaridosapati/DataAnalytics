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
