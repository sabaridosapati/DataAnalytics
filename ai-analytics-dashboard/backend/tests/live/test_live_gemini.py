"""Real Gemini smoke test. Costs a little. Run manually only:

    docker compose run --rm -e LIVE_GEMINI_API_KEY=... backend pytest -m live tests/live -q
"""
import os

import pytest

from app.config import Settings
from app.llm.gemini_provider import GeminiProvider

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("LIVE_GEMINI_API_KEY"), reason="LIVE_GEMINI_API_KEY not set")
async def test_gemini_json_and_embeddings():
    provider = GeminiProvider(Settings(llm_provider="gemini", gemini_api_key=os.environ["LIVE_GEMINI_API_KEY"]))
    out = await provider.chat_json(
        purpose="ping",
        system="Return the requested JSON.",
        user="User question: reply with ok=true",
        schema={"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
    )
    assert out == {"ok": True}
    assert len((await provider.embed(["hello"]))[0]) == 1536


# ---- OpenAI version (re-enable when switching back) ----
# """Real OpenAI smoke test. Costs a few cents. Run manually only:
#
#     docker compose run --rm -e LIVE_OPENAI_API_KEY=sk-... backend pytest -m live tests/live -q
# """
# import os
#
# import pytest
#
# from app.config import Settings
# from app.llm.openai_provider import OpenAIProvider
#
# pytestmark = pytest.mark.live
#
#
# @pytest.mark.skipif(not os.environ.get("LIVE_OPENAI_API_KEY"), reason="LIVE_OPENAI_API_KEY not set")
# async def test_openai_json_and_embeddings():
#     provider = OpenAIProvider(Settings(llm_provider="openai", openai_api_key=os.environ["LIVE_OPENAI_API_KEY"]))
#     out = await provider.chat_json(
#         purpose="ping",
#         system="Return the requested JSON.",
#         user="User question: reply with ok=true",
#         schema={"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
#     )
#     assert out == {"ok": True}
#     assert len((await provider.embed(["hello"]))[0]) == 1536
