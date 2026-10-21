from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

AgentName = Literal["sql", "rag", "compute", "viz"]


class PlanStep(BaseModel):
    id: str
    agent: AgentName
    task: str
    datasets: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    intent: str
    answerable: bool
    reason: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)


@dataclass
class StepResult:
    id: str
    agent: str
    status: Literal["ok", "error", "skipped"]
    output: dict | None = None
    error: str | None = None
    ms: int = 0


class StepError(Exception):
    """An agent could not complete its step; the message is shown to the user."""


@dataclass
class AgentContext:
    llm: Any
    settings: Any
    catalog: Any
    state: Any
    question: str


AgentFn = Callable[[PlanStep, list[StepResult], AgentContext], Awaitable[dict]]
