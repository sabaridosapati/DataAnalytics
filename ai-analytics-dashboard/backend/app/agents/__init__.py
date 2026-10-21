from app.agents.compute_agent import run_compute_agent
from app.agents.rag_agent import run_rag_agent
from app.agents.sql_agent import run_sql_agent
from app.agents.viz_agent import run_viz_agent

AGENTS = {
    "sql": run_sql_agent,
    "rag": run_rag_agent,
    "compute": run_compute_agent,
    "viz": run_viz_agent,
}
