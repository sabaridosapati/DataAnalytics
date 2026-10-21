import asyncio

import pytest
from sqlalchemy import select

from app.chats import create_chat, get_owned_chat, load_context, make_title, save_turn
from app.db.models import Chat, ChatTurn, User


def canned(answer="Total is 42.", answerable=True):
    return {"answer": answer, "answerable": answerable, "unverified_numbers": [], "charts": [], "tables": [],
            "sources": [], "sql": [], "plan": None, "steps": []}


async def make_user(db, email="u@example.com") -> int:
    async with db.sessionmaker() as s:
        u = User(email=email, password_hash="x", role="user", is_verified=True)
        s.add(u)
        await s.commit()
        return u.id


def test_make_title_collapses_whitespace_and_truncates():
    assert make_title("  What   is\n the  total? ") == "What is the total?"
    assert len(make_title("x" * 500)) == 120
    assert make_title("é" * 200) == "é" * 120
    assert make_title("   ") == "New chat"


async def test_create_chat_sets_owner_and_title(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "  revenue   by region ")
    async with db.sessionmaker() as s:
        chat = await get_owned_chat(s, chat_id, uid)
        assert chat.title == "revenue by region" and chat.user_id == uid
        assert await get_owned_chat(s, chat_id, uid + 999) is None
        assert await get_owned_chat(s, chat_id + 999, uid) is None


async def test_save_turn_assigns_increasing_indexes_and_bumps_updated_at(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q1")
    async with db.sessionmaker() as s:
        before = (await s.get(Chat, chat_id)).updated_at
    t1 = await save_turn(db.sessionmaker, chat_id, uid, question="q1", resp=canned(), status="ok", error=None, latency_ms=5, cache_hit=False)
    t2 = await save_turn(db.sessionmaker, chat_id, uid, question="q2", resp=None, status="timeout", error="exceeded 150s", latency_ms=9, cache_hit=False)
    assert t1 and t2 and t1 != t2
    async with db.sessionmaker() as s:
        turns = (await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat_id).order_by(ChatTurn.turn_index))).all()
        assert [t.turn_index for t in turns] == [0, 1]
        assert turns[1].response is None and turns[1].status == "timeout" and turns[1].error == "exceeded 150s"
        assert (await s.get(Chat, chat_id)).updated_at > before


async def test_load_context_returns_last_three_answerable_oldest_first(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q0")
    for i in range(5):
        await save_turn(db.sessionmaker, chat_id, uid, question=f"q{i}", resp=canned(f"a{i}"), status="ok", error=None, latency_ms=1, cache_hit=False)
    await save_turn(db.sessionmaker, chat_id, uid, question="unanswerable", resp=canned("nope", answerable=False), status="unanswerable", error=None, latency_ms=1, cache_hit=False)
    await save_turn(db.sessionmaker, chat_id, uid, question="failed", resp=None, status="timeout", error="x", latency_ms=1, cache_hit=False)
    ctx = await load_context(db.sessionmaker, chat_id, uid)
    assert ctx == [{"question": f"q{i}", "answer": f"a{i}"} for i in (2, 3, 4)]


async def test_load_context_truncates_long_answers(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q")
    await save_turn(db.sessionmaker, chat_id, uid, question="q", resp=canned("a" * 9000), status="ok", error=None, latency_ms=1, cache_hit=False)
    assert len((await load_context(db.sessionmaker, chat_id, uid))[0]["answer"]) == 4000


async def test_concurrent_saves_never_raise_and_indexes_stay_unique(db):
    uid = await make_user(db)
    chat_id = await create_chat(db.sessionmaker, uid, "q")
    results = await asyncio.gather(*[
        save_turn(db.sessionmaker, chat_id, uid, question=f"q{i}", resp=canned(), status="ok", error=None, latency_ms=1, cache_hit=False)
        for i in range(5)
    ])
    assert any(r is not None for r in results)
    async with db.sessionmaker() as s:
        idx = (await s.scalars(select(ChatTurn.turn_index).where(ChatTurn.chat_id == chat_id))).all()
        assert len(idx) == len(set(idx))


async def test_load_context_and_save_turn_enforce_ownership(db):
    owner = await make_user(db)
    intruder = await make_user(db, "intruder@example.com")
    chat_id = await create_chat(db.sessionmaker, owner, "q")
    await save_turn(db.sessionmaker, chat_id, owner, question="q", resp=canned("a"), status="ok", error=None, latency_ms=1, cache_hit=False)
    assert await load_context(db.sessionmaker, chat_id, intruder) == []
    assert len(await load_context(db.sessionmaker, chat_id, owner)) == 1
    stolen = await save_turn(db.sessionmaker, chat_id, intruder, question="x", resp=canned(), status="ok", error=None, latency_ms=1, cache_hit=False)
    assert stolen is None
    async with db.sessionmaker() as s:
        assert len((await s.scalars(select(ChatTurn).where(ChatTurn.chat_id == chat_id))).all()) == 1
