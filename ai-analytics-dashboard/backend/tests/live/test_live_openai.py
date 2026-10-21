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
