from sqlalchemy import select

from app.chats import create_chat, save_turn
from app.db.models import Chat, ChatTurn, User
from helpers import auth, register_verified_user


def canned(answer="Total is 42.", answerable=True):
    return {"answer": answer, "answerable": answerable, "unverified_numbers": [], "charts": [], "tables": [],
            "sources": [], "sql": [], "plan": None, "steps": []}


async def admin_id(client) -> int:
    async with client.app.state.sessionmaker() as s:
        return (await s.scalar(select(User).where(User.role == "admin"))).id


async def seed_chat(client, user_id: int, question: str, turns: int = 1) -> int:
    sm = client.app.state.sessionmaker
    chat_id = await create_chat(sm, user_id, question)
    for i in range(turns):
        await save_turn(sm, chat_id, user_id, question=f"{question} #{i}", resp=canned(f"a{i}"), status="ok", error=None, latency_ms=3, cache_hit=False)
    return chat_id


async def test_requires_auth(client):
    assert (await client.get("/api/chats")).status_code == 401
    assert (await client.get("/api/chats/1")).status_code == 401
    assert (await client.delete("/api/chats/1")).status_code == 401


async def test_list_returns_only_own_chats_newest_first_with_turn_count(client, admin_token):
    admin = await admin_id(client)
    first = await seed_chat(client, admin, "first", turns=2)
    second = await seed_chat(client, admin, "second", turns=1)
    _, other_token = await register_verified_user(client)
    async with client.app.state.sessionmaker() as s:
        other = (await s.scalar(select(User).where(User.role == "user"))).id
    await seed_chat(client, other, "not yours")

    body = (await client.get("/api/chats", headers=auth(admin_token))).json()
    assert body["total"] == 2
    assert [c["id"] for c in body["items"]] == [second, first]
    assert [c["turn_count"] for c in body["items"]] == [1, 2]
    assert {"id", "title", "updated_at", "turn_count"} <= set(body["items"][0])

    theirs = (await client.get("/api/chats", headers=auth(other_token))).json()
    assert [c["title"] for c in theirs["items"]] == ["not yours"]


async def test_get_returns_turns_in_order(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "topic", turns=3)
    body = (await client.get(f"/api/chats/{chat_id}", headers=auth(admin_token))).json()
    assert body["title"] == "topic"
    assert [t["turn_index"] for t in body["turns"]] == [0, 1, 2]
    assert body["turns"][0]["question"] == "topic #0"
    assert body["turns"][0]["response"]["answer"] == "a0"
    assert body["turns"][0]["status"] == "ok"


async def test_get_and_delete_of_foreign_or_missing_chat_is_404(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "private")
    _, other_token = await register_verified_user(client)
    for r in (
        await client.get(f"/api/chats/{chat_id}", headers=auth(other_token)),
        await client.delete(f"/api/chats/{chat_id}", headers=auth(other_token)),
        await client.get("/api/chats/999999", headers=auth(admin_token)),
        await client.delete("/api/chats/999999", headers=auth(admin_token)),
    ):
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    # the real chat survived the foreign delete attempt
    assert (await client.get(f"/api/chats/{chat_id}", headers=auth(admin_token))).status_code == 200


async def test_delete_cascades_turns(client, admin_token):
    chat_id = await seed_chat(client, await admin_id(client), "gone", turns=2)
    r = await client.delete(f"/api/chats/{chat_id}", headers=auth(admin_token))
    assert r.status_code == 204
    async with client.app.state.sessionmaker() as s:
        assert await s.get(Chat, chat_id) is None
        assert (await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat_id))).all() == []


async def test_list_pagination(client, admin_token):
    admin = await admin_id(client)
    for i in range(3):
        await seed_chat(client, admin, f"c{i}")
    page = (await client.get("/api/chats?limit=2&offset=1", headers=auth(admin_token))).json()
    assert page["total"] == 3 and len(page["items"]) == 2
    assert (await client.get("/api/chats?limit=0", headers=auth(admin_token))).status_code == 422


import pytest

from app.llm.provider import LLMError


@pytest.fixture
def fake_answer(monkeypatch):
    """Replace the agent pipeline; record the history the endpoint passes in."""
    seen = []

    async def _answer(state, question, history):
        seen.append({"question": question, "history": history})
        if "boom" in question:
            raise LLMError("simulated outage")
        return canned(f"Answer to: {question}")

    monkeypatch.setattr("app.api.query.answer_question", _answer)
    return seen


async def ask(client, token, question, **extra):
    return await client.post("/api/query", json={"question": question, **extra}, headers=auth(token))


async def test_first_question_creates_chat_with_turn(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "How many orders?")
    assert r.status_code == 200
    body = r.json()
    assert body["chat_id"] and body["turn_id"]
    detail = (await client.get(f"/api/chats/{body['chat_id']}", headers=auth(admin_token))).json()
    assert detail["title"] == "How many orders?"
    assert [t["question"] for t in detail["turns"]] == ["How many orders?"]
    assert detail["turns"][0]["response"]["answer"] == "Answer to: How many orders?"
    assert detail["turns"][0]["status"] == "ok"


async def test_followup_appends_and_uses_server_side_history(client, admin_token, fake_answer):
    first = (await ask(client, admin_token, "first question")).json()
    second = (await ask(client, admin_token, "second question", chat_id=first["chat_id"],
                        history=[{"question": "client lies", "answer": "ignored"}])).json()
    assert second["chat_id"] == first["chat_id"]
    assert fake_answer[1]["history"] == [{"question": "first question", "answer": "Answer to: first question"}]
    detail = (await client.get(f"/api/chats/{first['chat_id']}", headers=auth(admin_token))).json()
    assert [t["turn_index"] for t in detail["turns"]] == [0, 1]


async def test_without_chat_id_legacy_history_still_reaches_planner(client, admin_token, fake_answer):
    await ask(client, admin_token, "legacy", history=[{"question": "hi", "answer": "hello"}])
    assert fake_answer[0]["history"] == [{"question": "hi", "answer": "hello"}]


async def test_foreign_chat_id_is_404_and_writes_nothing(client, admin_token, fake_answer):
    mine = (await ask(client, admin_token, "mine")).json()["chat_id"]
    _, other_token = await register_verified_user(client)
    r = await ask(client, other_token, "sneaky", chat_id=mine)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert len(fake_answer) == 1  # pipeline never ran for the foreign request
    async with client.app.state.sessionmaker() as s:
        assert len((await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == mine))).all()) == 1
        assert len((await s.scalars(select(Chat))).all()) == 1


async def test_failed_question_is_saved_and_error_carries_chat_id(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "boom please")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "llm_unavailable" and err["chat_id"]
    detail = (await client.get(f"/api/chats/{err['chat_id']}", headers=auth(admin_token))).json()
    turn = detail["turns"][0]
    assert turn["response"] is None and turn["status"] == "llm_unavailable" and "simulated outage" in turn["error"]
    # a later question can continue the same chat
    ok = await ask(client, admin_token, "recover", chat_id=err["chat_id"])
    assert ok.status_code == 200 and ok.json()["chat_id"] == err["chat_id"]


async def test_cache_hit_still_records_a_turn(client, admin_token, fake_answer):
    a = (await ask(client, admin_token, "cache me")).json()
    b = (await ask(client, admin_token, "cache me")).json()
    assert a["cache_hit"] is False and b["cache_hit"] is True
    assert len(fake_answer) == 1
    assert a["chat_id"] != b["chat_id"]  # each fresh question without chat_id starts its own chat
    detail = (await client.get(f"/api/chats/{b['chat_id']}", headers=auth(admin_token))).json()
    assert detail["turns"][0]["response"]["answer"] == "Answer to: cache me"


async def test_non_integer_chat_id_is_validation_error(client, admin_token, fake_answer):
    r = await ask(client, admin_token, "x", chat_id="abc")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_save_failure_does_not_break_the_query(client, admin_token, fake_answer, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.chats.ChatTurn", broken)  # make save_turn's insert blow up
    r = await ask(client, admin_token, "still works")
    assert r.status_code == 200 and r.json()["answer"] == "Answer to: still works"
    assert r.json()["turn_id"] is None


async def test_query_audit_still_written(client, admin_token, fake_answer):
    from app.db.models import QueryAudit

    await ask(client, admin_token, "audited")
    async with client.app.state.sessionmaker() as s:
        assert (await s.scalar(select(QueryAudit.question))) == "audited"


async def test_unexpected_failure_is_recorded_as_error_with_chat_id(client, admin_token, monkeypatch):
    async def _boom(state, question, history):
        raise RuntimeError("redis exploded")

    monkeypatch.setattr("app.api.query.answer_question", _boom)
    r = await ask(client, admin_token, "kaboom")
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "internal_error" and err["chat_id"]
    detail = (await client.get(f"/api/chats/{err['chat_id']}", headers=auth(admin_token))).json()
    turn = detail["turns"][0]
    assert turn["status"] == "error" and turn["response"] is None and "redis exploded" in turn["error"]


async def test_get_merges_cache_hit_into_response(client, admin_token, fake_answer):
    await ask(client, admin_token, "cache me too")
    b = (await ask(client, admin_token, "cache me too")).json()
    detail = (await client.get(f"/api/chats/{b['chat_id']}", headers=auth(admin_token))).json()
    assert detail["turns"][0]["response"]["cache_hit"] is True
    a = (await client.get(f"/api/chats/{(await ask(client, admin_token, 'fresh one')).json()['chat_id']}", headers=auth(admin_token))).json()
    assert a["turns"][0]["response"]["cache_hit"] is False


async def test_failed_turn_response_stays_null_without_cache_hit_merge(client, admin_token, fake_answer):
    err = (await ask(client, admin_token, "boom again")).json()["error"]
    detail = (await client.get(f"/api/chats/{err['chat_id']}", headers=auth(admin_token))).json()
    assert detail["turns"][0]["response"] is None


async def test_out_of_range_ids_are_validation_errors(client, admin_token, fake_answer):
    big = 2**40
    for r in (
        await client.get(f"/api/chats/{big}", headers=auth(admin_token)),
        await client.delete(f"/api/chats/{big}", headers=auth(admin_token)),
        await client.get("/api/chats/0", headers=auth(admin_token)),
        await ask(client, admin_token, "x", chat_id=big),
        await ask(client, admin_token, "x", chat_id=0),
    ):
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
