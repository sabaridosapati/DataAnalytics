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
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        build_provider(Settings(llm_provider="gemini", gemini_api_key=""))


def test_build_provider_fake():
    assert isinstance(build_provider(Settings(llm_provider="fake")), FakeLLMProvider)


def test_build_provider_unknown():
    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        build_provider(Settings(llm_provider="nope"))


@pytest.mark.parametrize("method", ["chat_json", "chat_text", "embed"])
@pytest.mark.parametrize("code,status,quota", [(429, "RESOURCE_EXHAUSTED", True), (500, "INTERNAL", False)])
async def test_gemini_maps_quota_errors(method, code, status, quota):
    from types import SimpleNamespace

    from app.llm.gemini_provider import GeminiProvider
    from app.llm.provider import LLMQuotaError

    class Boom(Exception):
        pass

    async def fail(**kwargs):
        e = Boom("test failure")
        e.code, e.status = code, status
        raise e

    provider = GeminiProvider(Settings(gemini_api_key="test-only"))
    provider.client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=fail, embed_content=fail)))
    kwargs = {"texts": ["hello"]} if method == "embed" else {"purpose": "test", "system": "", "user": "hello"}
    if method == "chat_json":
        kwargs["schema"] = {"type": "object"}
    with pytest.raises(LLMError) as caught:
        await getattr(provider, method)(**kwargs)
    assert isinstance(caught.value, LLMQuotaError) == quota


# ---- OpenAI version of the test above (re-enable when switching back to OpenAI) ----
# @pytest.mark.parametrize("method", ["chat_json", "chat_text", "embed"])
# @pytest.mark.parametrize("code", ["credit_balance_exhausted", "insufficient_quota", "rate_limit_exceeded"])
# async def test_openai_distinguishes_exhausted_credits_from_rate_limits(method, code):
#     import httpx
#     from openai import AsyncOpenAI
#
#     from app.llm.openai_provider import OpenAIProvider
#     from app.llm.provider import LLMQuotaError
#
#     def respond(request):
#         return httpx.Response(429, json={"error": {"code": code, "type": code, "message": "test failure"}})
#
#     provider = OpenAIProvider(Settings(openai_api_key="test-only"))
#     await provider.client.close()
#     async with AsyncOpenAI(
#         api_key="test-only", max_retries=0,
#         http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
#     ) as client:
#         provider.client = client
#         kwargs = {"texts": ["hello"]} if method == "embed" else {"purpose": "test", "system": "", "user": "hello"}
#         if method == "chat_json":
#             kwargs["schema"] = {"type": "object"}
#         with pytest.raises(LLMError) as caught:
#             await getattr(provider, method)(**kwargs)
#         assert isinstance(caught.value, LLMQuotaError) == (code != "rate_limit_exceeded")
