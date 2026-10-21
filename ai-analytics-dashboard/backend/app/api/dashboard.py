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
