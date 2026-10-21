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
