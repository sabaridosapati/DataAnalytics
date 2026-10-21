# Task 10: Query API, orchestration, dashboard pins, admin API, demo mode

**Files:**
- Create: `backend/app/agents/orchestrator.py`, `backend/app/api/query.py`, `backend/app/api/dashboard.py`, `backend/app/api/admin.py`
- Replace: `backend/app/llm/demo_script.py` (real demo rules)
- Modify: `backend/app/main.py` (register routers)
- Create: `backend/tests/live/test_live_openai.py` (opt-in, never run in this build)
- Test: `backend/tests/integration/test_query_api.py`, `backend/tests/integration/test_dashboard_admin.py`

**Interfaces:**
- Consumes: everything above.
- Produces `app.agents.orchestrator.async answer_question(state, question, history) -> dict` — keys `answer, answerable, unverified_numbers, charts, tables, sources, sql, plan, steps`.
- Produces endpoints:
  - `POST /api/query` `{question (1..2000 chars), history: [{question, answer}] (≤10)}` → answer dict + `cache_hit: bool`, `request_id: str`. 422 `plan_failed` when no valid plan; 502 `llm_unavailable` when the LLM fails during planning.
  - `GET/POST /api/dashboard/pins`, `DELETE /api/dashboard/pins/{id}` (creator or admin), `POST /api/dashboard/pins/{id}/refresh`.
  - `GET /api/admin/users`, `PATCH /api/admin/users/{id}` `{is_active?, role?}`, `GET /api/admin/audit?limit&offset` → `{items, total}`.
- Pin = `{id, title, chart, sql, created_by, created_at}`.
- Demo questions (fake provider), used by README and the frontend's example chips:
  1. `What is the total revenue by region?`
  2. `Show the monthly revenue trend`
  3. `Why did Q3 performance drop?`
  4. `What is the average salary by department?`
  5. `Show the monthly revenue trend and explain the Q3 dip`

- [ ] **Step 1: Write the failing integration tests**

**File: `backend/tests/integration/test_query_api.py`**
```python
import json

import pytest
from sqlalchemy import select

from app.catalog import load_catalog
from app.db.models import Dataset, QueryAudit
from app.llm.demo_script import DEMO_QUESTIONS
from app.llm.fake_provider import Rule
from app.llm.provider import LLMError
from helpers import auth, register_verified_user, upload_and_wait


@pytest.fixture
async def loaded(client, admin_token, fixtures_dir):
    await upload_and_wait(
        client, admin_token,
        [fixtures_dir / "sales_2024.csv", fixtures_dir / "employees.json", fixtures_dir / "annual_report_2024.pdf"],
    )
    return json.loads((fixtures_dir / "expected_answers.json").read_text())


def plan(*steps, answerable=True, reason=None):
    return {"intent": "test", "answerable": answerable, "reason": reason, "steps": list(steps)}


def step(id, agent, datasets=(), depends_on=(), task="t"):
    return {"id": id, "agent": agent, "task": task, "datasets": list(datasets), "depends_on": list(depends_on)}


async def ask(client, token, question, history=None):
    return await client.post("/api/query", json={"question": question, "history": history or []}, headers=auth(token))


@pytest.mark.parametrize("question", DEMO_QUESTIONS)
async def test_demo_questions_work_end_to_end(client, admin_token, loaded, question):
    r = await ask(client, admin_token, question)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answerable"] is True
    assert all(s["status"] == "ok" for s in body["steps"]), body["steps"]
    assert body["unverified_numbers"] == []
    assert body["answer"]


async def test_sql_answer_matches_ground_truth(client, admin_token, loaded):
    r = await ask(client, admin_token, "What is the total revenue by region?")
    body = r.json()
    table = body["tables"][0]
    got = {row[0]: row[1] for row in table["rows"]}
    for region, total in loaded["revenue_by_region"].items():
        assert got[region] == pytest.approx(total, abs=0.01)
    assert body["charts"][0]["type"] == "bar" and body["charts"][0]["source_sql"]
    assert body["sql"][0].startswith("SELECT region")


async def test_multi_agent_combines_outputs(client, admin_token, loaded):
    body = (await ask(client, admin_token, "Show the monthly revenue trend and explain the Q3 dip")).json()
    assert [s["agent"] for s in body["steps"]] == ["sql", "compute", "viz", "rag"]
    assert body["charts"][0]["type"] == "line"
    assert body["sources"] and body["sources"][0]["source"] == "annual_report_2024.pdf"
    assert "supply-chain" in body["answer"]


async def test_unanswerable_question(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [Rule("intent", "2019", plan(answerable=False, reason="The data only covers 2024."))]
    body = (await ask(client, admin_token, "What was revenue in 2019?")).json()
    assert body["answerable"] is False and body["answer"] == "The data only covers 2024." and body["steps"] == []


async def test_malicious_sql_is_blocked_and_reported(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [
        Rule("intent", "wipe", plan(step("s1", "sql", ["sales_2024"]))),
        Rule("sql", "wipe", {"sql": "DELETE FROM data.sales_2024", "explanation": ""}),
    ]
    body = (await ask(client, admin_token, "Please wipe the sales table")).json()
    assert body["steps"][0]["status"] == "error" and "Only SELECT" in body["steps"][0]["error"]
    assert "couldn't answer" in body["answer"]
    async with client.app.state.ro_pool.acquire() as conn:
        assert await conn.fetchval("SELECT COUNT(*) FROM data.sales_2024") == 2000


async def test_cache_hit_then_invalidated_by_upload(client, admin_token, loaded, fixtures_dir):
    q = "What is the total revenue by region?"
    first = (await ask(client, admin_token, q)).json()
    calls = len(client.app.state.llm.calls)
    second = (await ask(client, admin_token, "  what is the TOTAL revenue by region  ")).json()
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert len(client.app.state.llm.calls) == calls
    await upload_and_wait(client, admin_token, [fixtures_dir / "employees.json"])
    third = (await ask(client, admin_token, q)).json()
    assert third["cache_hit"] is False


async def test_history_disables_answer_cache(client, admin_token, loaded):
    q = "What is the total revenue by region?"
    await ask(client, admin_token, q)
    body = (await ask(client, admin_token, q, history=[{"question": "hi", "answer": "hello"}])).json()
    assert body["cache_hit"] is False


async def test_catalog_excludes_datasets_not_ready(client, admin_token, loaded):
    state = client.app.state
    async with state.sessionmaker() as s:
        s.add(Dataset(name="half.csv", slug="half_loaded", kind="table", source_filename="half.csv", file_type="csv", status="processing"))
        await s.commit()
    catalog = await load_catalog(state)
    assert catalog.get("half_loaded") is None and catalog.get("sales_2024") is not None


async def test_llm_outage_returns_502(client, admin_token, loaded):
    def boom(user):
        raise LLMError("simulated outage")

    client.app.state.llm.rules[:0] = [Rule("intent", "outage", boom)]
    r = await ask(client, admin_token, "trigger an outage please")
    assert r.status_code == 502 and r.json()["error"]["code"] == "llm_unavailable"


async def test_invalid_plan_returns_422(client, admin_token, loaded):
    client.app.state.llm.rules[:0] = [Rule("intent", "bogus", plan(step("s1", "sql", ["no_such_table"])))]
    r = await ask(client, admin_token, "bogus question")
    assert r.status_code == 422 and r.json()["error"]["code"] == "plan_failed"


async def test_no_datasets_is_unanswerable(client, admin_token):
    body = (await ask(client, admin_token, "What is the total revenue by region?")).json()
    assert body["answerable"] is False and "Upload" in body["answer"]


async def test_queries_are_audited(client, admin_token, loaded):
    await ask(client, admin_token, "What is the total revenue by region?")
    async with client.app.state.sessionmaker() as s:
        rows = (await s.scalars(select(QueryAudit))).all()
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].sql_executed and rows[0].agents_used == ["sql", "viz"]


async def test_regular_user_can_query_and_input_is_validated(client, loaded):
    _, token = await register_verified_user(client)
    assert (await ask(client, token, "What is the total revenue by region?")).status_code == 200
    r = await client.post("/api/query", json={"question": ""}, headers=auth(token))
    assert r.status_code == 422
    assert (await client.post("/api/query", json={"question": "x"})).status_code == 401
```

**File: `backend/tests/integration/test_dashboard_admin.py`**
```python
from helpers import auth, register_verified_user, upload_and_wait


async def pin_region_chart(client, token):
    body = (await client.post("/api/query", json={"question": "What is the total revenue by region?"}, headers=auth(token))).json()
    chart = body["charts"][0]
    r = await client.post("/api/dashboard/pins", json={"title": "Revenue by region", "chart": chart}, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()


async def test_pin_list_refresh_delete(client, admin_token, fixtures_dir):
    await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    pin = await pin_region_chart(client, admin_token)
    assert pin["sql"].startswith("SELECT region")
    _, user_token = await register_verified_user(client)
    pins = (await client.get("/api/dashboard/pins", headers=auth(user_token))).json()
    assert [p["id"] for p in pins] == [pin["id"]]  # shared workspace
    refreshed = await client.post(f"/api/dashboard/pins/{pin['id']}/refresh", headers=auth(user_token))
    assert refreshed.status_code == 200 and len(refreshed.json()["chart"]["data"]["rows"]) == 4
    assert (await client.delete(f"/api/dashboard/pins/{pin['id']}", headers=auth(user_token))).status_code == 403
    assert (await client.delete(f"/api/dashboard/pins/{pin['id']}", headers=auth(admin_token))).status_code == 204


async def test_pin_with_tampered_sql_is_not_refreshable(client, admin_token, fixtures_dir):
    await upload_and_wait(client, admin_token, [fixtures_dir / "sales_2024.csv"])
    chart = {"type": "table", "title": "x", "x": None, "y": [], "series": None,
             "data": {"columns": ["a"], "rows": [[1]]}, "source_sql": "DELETE FROM data.sales_2024"}
    pin = (await client.post("/api/dashboard/pins", json={"title": "evil", "chart": chart}, headers=auth(admin_token))).json()
    assert pin["sql"] is None
    r = await client.post(f"/api/dashboard/pins/{pin['id']}/refresh", headers=auth(admin_token))
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_refreshable"


async def test_admin_endpoints(client, admin_token):
    email, user_token = await register_verified_user(client)
    assert (await client.get("/api/admin/users", headers=auth(user_token))).status_code == 403
    users = (await client.get("/api/admin/users", headers=auth(admin_token))).json()
    target = next(u for u in users if u["email"] == email)
    admin = next(u for u in users if u["username"] == "admin")
    r = await client.patch(f"/api/admin/users/{admin['id']}", json={"is_active": False}, headers=auth(admin_token))
    assert r.status_code == 400  # cannot change yourself
    r = await client.patch(f"/api/admin/users/{target['id']}", json={"is_active": False}, headers=auth(admin_token))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (await client.get("/api/auth/me", headers=auth(user_token))).status_code == 401
    audit = (await client.get("/api/admin/audit?limit=10&offset=0", headers=auth(admin_token))).json()
    assert audit == {"items": [], "total": 0}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_query_api.py tests/integration/test_dashboard_admin.py -q`
Expected: FAIL — `ImportError: cannot import name 'DEMO_QUESTIONS'`.

- [ ] **Step 3: Write the demo script (scripted fake-LLM responses)**

**File: `backend/app/llm/demo_script.py`**
```python
"""Scripted responses for LLM_PROVIDER=fake: a free demo over the sample datasets.

Only the questions in DEMO_QUESTIONS are understood; anything else gets a helpful 'demo mode' reply.
"""
import json
import re

from app.llm.fake_provider import Rule

DEMO_QUESTIONS = [
    "What is the total revenue by region?",
    "Show the monthly revenue trend",
    "Why did Q3 performance drop?",
    "What is the average salary by department?",
    "Show the monthly revenue trend and explain the Q3 dip",
]


def _step(id, agent, task, datasets=(), depends_on=()):
    return {"id": id, "agent": agent, "task": task, "datasets": list(datasets), "depends_on": list(depends_on)}


def _plan(*steps, intent="analytics"):
    return {"intent": intent, "answerable": True, "reason": None, "steps": list(steps)}


SQL_REGION = "SELECT region, ROUND(SUM(revenue)::numeric, 2) AS total_revenue FROM data.sales_2024 GROUP BY region ORDER BY total_revenue DESC"
SQL_MONTHLY = "SELECT date_trunc('month', order_date)::date AS month, ROUND(SUM(revenue)::numeric, 2) AS revenue FROM data.sales_2024 GROUP BY 1 ORDER BY 1"
SQL_SALARY = "SELECT department, ROUND(AVG(salary)::numeric, 2) AS avg_salary, COUNT(*) AS headcount FROM data.employees GROUP BY department ORDER BY avg_salary DESC"

PLAN_REGION = _plan(
    _step("s1", "sql", "Total revenue per region", ["sales_2024"]),
    _step("s2", "viz", "Bar chart of total revenue by region", depends_on=["s1"]),
)
PLAN_TREND = _plan(
    _step("s1", "sql", "Monthly revenue for 2024", ["sales_2024"]),
    _step("s2", "compute", "Month-over-month revenue growth in percent", depends_on=["s1"]),
    _step("s3", "viz", "Line chart of monthly revenue", depends_on=["s2"]),
)
PLAN_Q3 = _plan(_step("s1", "rag", "Find the reasons given for weak Q3 performance", ["annual_report_2024"]))
PLAN_SALARY = _plan(
    _step("s1", "sql", "Average salary and headcount per department", ["employees"]),
    _step("s2", "viz", "Bar chart of average salary by department", depends_on=["s1"]),
)
PLAN_TREND_EXPLAIN = _plan(
    _step("s1", "sql", "Monthly revenue for 2024", ["sales_2024"]),
    _step("s2", "compute", "Month-over-month revenue growth in percent", depends_on=["s1"]),
    _step("s3", "viz", "Line chart of monthly revenue", depends_on=["s2"]),
    _step("s4", "rag", "Find the reasons given for the Q3 revenue dip", ["annual_report_2024"]),
)
PLAN_UNKNOWN = {
    "intent": "demo_unknown",
    "answerable": False,
    "reason": "Demo mode (LLM_PROVIDER=fake) only understands the sample questions: "
    + " | ".join(DEMO_QUESTIONS)
    + ". Set LLM_PROVIDER=openai with your API key to ask anything.",
    "steps": [],
}

PCT_CHANGE = {
    "operations": [
        {"op": "pct_change", "column": "revenue", "columns": [], "group_by": [], "agg": None,
         "expression": None, "alias": "mom_growth_pct", "window": None, "q": None}
    ]
}


def _viz(kind, title, x, y):
    return {"type": kind, "title": title, "x": x, "y": y, "series": None}


def demo_rag(user: str) -> dict:
    excerpts = re.findall(r'<excerpt id="(\d+)"[^>]*>(.*?)</excerpt>', user, flags=re.DOTALL)
    relevant = [int(i) for i, body in excerpts if "supply" in body.lower() or "strike" in body.lower()]
    if not relevant:
        return {"answer": "The documents do not cover this.", "found": False, "citations": []}
    return {
        "answer": "According to the annual report, Q3 revenue declined because of supply-chain disruptions at the "
        "primary laptop supplier and a port strike that delayed furniture shipments.",
        "found": True,
        "citations": relevant[:2],
    }


def demo_answer(user: str) -> str:
    try:
        steps = json.loads(user.split("Step outputs:\n", 1)[1])
    except (IndexError, json.JSONDecodeError):
        return "Here are the results."
    lines = ["Demo mode answer (scripted, no LLM used):"]
    for s in steps:
        out = s.get("output") or {}
        if s.get("status") != "ok":
            lines.append(f"- The {s.get('agent')} step failed: {s.get('error')}")
            continue
        if out.get("answer"):
            lines.append(f"- From the documents: {out['answer']}")
        elif s.get("agent") in ("sql", "compute") and out.get("rows"):
            cols = out.get("columns", [])
            for row in out["rows"][:6]:
                lines.append("- " + ", ".join(f"{c}: {v}" for c, v in zip(cols, row)))
        for k, v in (out.get("scalars") or {}).items():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


DEMO_RULES = [
    Rule("intent", "explain", PLAN_TREND_EXPLAIN),
    Rule("intent", "revenue by region", PLAN_REGION),
    Rule("intent", "monthly revenue", PLAN_TREND),
    Rule("intent", "q3", PLAN_Q3),
    Rule("intent", "salary", PLAN_SALARY),
    Rule("intent", "", PLAN_UNKNOWN),
    Rule("sql", "revenue by region", {"sql": SQL_REGION, "explanation": "Sum of revenue per region."}),
    Rule("sql", "monthly revenue", {"sql": SQL_MONTHLY, "explanation": "Revenue per month."}),
    Rule("sql", "salary", {"sql": SQL_SALARY, "explanation": "Average salary per department."}),
    Rule("compute", "", PCT_CHANGE),
    Rule("viz", "revenue by region", _viz("bar", "Total revenue by region", "region", ["total_revenue"])),
    Rule("viz", "monthly revenue", _viz("line", "Monthly revenue 2024", "month", ["revenue"])),
    Rule("viz", "salary", _viz("bar", "Average salary by department", "department", ["avg_salary"])),
    Rule("rag", "", demo_rag),
    Rule("aggregate", "", demo_answer),
]
```

- [ ] **Step 4: Implement the orchestrator**

**File: `backend/app/agents/orchestrator.py`**
```python
from app.agents import AGENTS
from app.agents.aggregator import aggregate
from app.agents.executor import execute_plan
from app.agents.intent import analyze_intent
from app.agents.types import AgentContext
from app.catalog import load_catalog


def _empty_response(answer: str, plan: dict | None) -> dict:
    return {
        "answer": answer,
        "answerable": False,
        "unverified_numbers": [],
        "charts": [],
        "tables": [],
        "sources": [],
        "sql": [],
        "plan": plan,
        "steps": [],
    }


async def answer_question(state, question: str, history: list[dict]) -> dict:
    catalog = await load_catalog(state)
    plan = await analyze_intent(question, history, catalog, state.llm, state.settings)
    if not plan.answerable:
        return _empty_response(plan.reason or "This question cannot be answered from the available data.", plan.model_dump())
    ctx = AgentContext(llm=state.llm, settings=state.settings, catalog=catalog, state=state, question=question)
    results = await execute_plan(plan, AGENTS, ctx, state.settings.step_timeout_s)
    response = await aggregate(question, plan, results, ctx)
    response["answerable"] = True
    return response
```

- [ ] **Step 5: Implement the query, dashboard and admin routers**

**File: `backend/app/api/query.py`**
```python
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.agents.intent import PlanValidationError
from app.agents.orchestrator import answer_question
from app.api.deps import current_user
from app.api.errors import api_error
from app.api.ratelimit import limiter
from app.cache.redis_cache import answer_key
from app.db.models import QueryAudit, User
from app.db.versions import get_catalog_version
from app.llm.provider import LLMError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/query", tags=["query"])


class HistoryTurn(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=4000)


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


async def _audit(state, user_id: int, question: str, resp: dict | None, status: str, error: str | None, latency_ms: int, cache_hit: bool) -> None:
    try:
        async with state.sessionmaker() as s:
            s.add(
                QueryAudit(
                    user_id=user_id,
                    question=question[:2000],
                    plan_json={"plan": resp.get("plan"), "steps": resp.get("steps")} if resp else None,
                    sql_executed=resp.get("sql", []) if resp else [],
                    agents_used=[st["agent"] for st in resp.get("steps", [])] if resp else [],
                    latency_ms=latency_ms,
                    cache_hit=cache_hit,
                    status=status,
                    error=error,
                )
            )
            await s.commit()
    except Exception:  # noqa: BLE001 - auditing must never break a query
        log.exception("Failed to write query audit")


def _cacheable(resp: dict) -> bool:
    return resp.get("answerable") and not resp.get("unverified_numbers") and all(s["status"] == "ok" for s in resp.get("steps", []))


@router.post("")
@limiter.limit("30/minute")
async def query(request: Request, body: QueryIn, user: User = Depends(current_user)):
    state = request.app.state
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    question = " ".join(body.question.split())
    if not question:
        raise api_error(422, "validation_error", "question: must not be blank")
    history = [h.model_dump() for h in body.history[-3:]]

    async with state.sessionmaker() as s:
        version = await get_catalog_version(s)
    key = answer_key(version, question) if not history else None

    resp, status, error, cache_hit = None, "ok", None, False
    try:
        if key and (cached := await state.cache.get_json(key)) is not None:
            resp, cache_hit = cached, True
        else:
            resp = await answer_question(state, question, history)
            if not resp.get("answerable"):
                status = "unanswerable"
            elif any(s["status"] != "ok" for s in resp["steps"]):
                status = "partial"
            if key and _cacheable(resp):
                await state.cache.set_json(key, resp, state.settings.cache_ttl_s)
    except PlanValidationError as exc:
        status, error = "plan_failed", str(exc)
        raise api_error(422, "plan_failed", "I couldn't turn this question into a valid analysis plan. Try rephrasing it.")
    except LLMError as exc:
        status, error = "llm_unavailable", str(exc)
        raise api_error(502, "llm_unavailable", "The language model is unavailable right now. Please try again shortly.")
    finally:
        latency = int((time.perf_counter() - started) * 1000)
        await _audit(state, user.id, question, resp, status, error, latency, cache_hit)

    return {**resp, "cache_hit": cache_hit, "request_id": request_id}
```

**File: `backend/app/api/dashboard.py`**
```python
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sql_exec import QueryExecutionError, run_readonly_query
from app.api.deps import current_user, get_session
from app.api.errors import api_error
from app.catalog import load_catalog
from app.db.models import DashboardPin, User
from app.guardrails.sql_validator import SQLValidationError, validate_sql

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
MAX_CHART_BYTES = 2_000_000


class PinIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    chart: dict


class PinOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    chart: dict
    sql: str | None
    created_by: int | None
    created_at: datetime


async def _safe_sql(state, sql: str | None) -> str | None:
    if not sql:
        return None
    catalog = await load_catalog(state)
    try:
        return validate_sql(sql, catalog.sql_schema(), state.settings.max_sql_rows)
    except SQLValidationError:
        return None


@router.get("/pins", response_model=list[PinOut])
async def list_pins(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(DashboardPin).order_by(DashboardPin.created_at.desc(), DashboardPin.id.desc()))).all()


@router.post("/pins", status_code=201, response_model=PinOut)
async def create_pin(body: PinIn, request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    chart = body.chart
    if not isinstance(chart.get("data"), dict) or chart.get("type") not in {"bar", "line", "area", "pie", "scatter", "table", "kpi"}:
        raise api_error(422, "invalid_chart", "Chart must have a valid type and data.")
    if len(json.dumps(chart, default=str)) > MAX_CHART_BYTES:
        raise api_error(413, "chart_too_large", "This chart is too large to pin.")
    pin = DashboardPin(title=body.title, chart=chart, sql=await _safe_sql(request.app.state, chart.get("source_sql")), created_by=user.id)
    session.add(pin)
    await session.commit()
    return pin


@router.post("/pins/{pin_id}/refresh", response_model=PinOut)
async def refresh_pin(pin_id: int, request: Request, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    pin = await session.get(DashboardPin, pin_id)
    if pin is None:
        raise api_error(404, "not_found", "Pin not found.")
    state = request.app.state
    safe = await _safe_sql(state, pin.sql)
    if safe is None:
        raise api_error(400, "not_refreshable", "This chart has no stored query that can be re-run.")
    catalog = await load_catalog(state)
    try:
        result = await run_readonly_query(state, safe, catalog.version, state.settings)
    except QueryExecutionError as exc:
        raise api_error(409, "refresh_failed", f"The stored query no longer runs: {exc}")
    chart = dict(pin.chart)
    chart["data"] = {"columns": result["columns"], "rows": result["rows"][:1000]}
    pin.chart = chart
    await session.commit()
    return pin


@router.delete("/pins/{pin_id}", status_code=204)
async def delete_pin(pin_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    pin = await session.get(DashboardPin, pin_id)
    if pin is None:
        raise api_error(404, "not_found", "Pin not found.")
    if pin.created_by != user.id and user.role != "admin":
        raise api_error(403, "forbidden", "Only the creator or an admin can remove this pin.")
    await session.delete(pin)
    await session.commit()
    return Response(status_code=204)
```

**File: `backend/app/api/admin.py`**
```python
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_user, get_session
from app.api.errors import api_error
from app.db.models import QueryAudit, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    username: str | None
    role: str
    is_verified: bool
    is_active: bool
    created_at: datetime


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: Literal["admin", "user"] | None = None


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(User).order_by(User.id))).all()


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(user_id: int, body: UserPatch, admin: User = Depends(admin_user), session: AsyncSession = Depends(get_session)):
    if user_id == admin.id:
        raise api_error(400, "cannot_modify_self", "You cannot change your own account here.")
    user = await session.get(User, user_id)
    if user is None:
        raise api_error(404, "not_found", "User not found.")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role is not None:
        user.role = body.role
    await session.commit()
    return user


@router.get("/audit")
async def audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(admin_user),
    session: AsyncSession = Depends(get_session),
):
    total = await session.scalar(select(func.count()).select_from(QueryAudit))
    rows = (
        await session.execute(
            select(QueryAudit, User.email)
            .outerjoin(User, User.id == QueryAudit.user_id)
            .order_by(QueryAudit.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        {
            "id": a.id,
            "user_email": email,
            "question": a.question,
            "status": a.status,
            "error": a.error,
            "latency_ms": a.latency_ms,
            "cache_hit": a.cache_hit,
            "agents_used": a.agents_used,
            "sql_executed": a.sql_executed,
            "created_at": a.created_at.isoformat(),
        }
        for a, email in rows
    ]
    return {"items": items, "total": total or 0}
```

**Modify `backend/app/main.py`:**
```python
from app.api import admin, auth, dashboard, datasets, query
```
```python
ROUTERS = [auth.router, datasets.router, query.router, dashboard.router, admin.router]
```

- [ ] **Step 6: Add the opt-in live test (not run in this build)**

**File: `backend/tests/live/test_live_openai.py`**
```python
"""Real OpenAI smoke test. Costs a few cents. Run manually only:

    docker compose run --rm -e LIVE_OPENAI_API_KEY=sk-... backend pytest -m live tests/live -q
"""
import os

import pytest

from app.config import Settings
from app.llm.openai_provider import OpenAIProvider

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.environ.get("LIVE_OPENAI_API_KEY"), reason="LIVE_OPENAI_API_KEY not set")
async def test_openai_json_and_embeddings():
    provider = OpenAIProvider(Settings(llm_provider="openai", openai_api_key=os.environ["LIVE_OPENAI_API_KEY"]))
    out = await provider.chat_json(
        purpose="ping",
        system="Return the requested JSON.",
        user="User question: reply with ok=true",
        schema={"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}},
    )
    assert out == {"ok": True}
    assert len((await provider.embed(["hello"]))[0]) == 1536
```

> The global socket guard in `tests/conftest.py` blocks openai.com unless `LIVE_OPENAI_API_KEY` is set, so this test can only reach OpenAI when the user deliberately provides that variable. This build never sets it.

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `docker compose build backend; docker compose run --rm backend pytest tests/integration/test_query_api.py tests/integration/test_dashboard_admin.py -q`
Expected: all pass.

- [ ] **Step 8: Run the full backend suite**

Run: `docker compose run --rm backend pytest -q`
Expected: all pass, 0 network calls to OpenAI.

- [ ] **Step 9: Commit**

```bash
git add backend
git commit -m "feat(api): query orchestration endpoint, caching, audit, dashboard pins, admin, demo mode"
```
