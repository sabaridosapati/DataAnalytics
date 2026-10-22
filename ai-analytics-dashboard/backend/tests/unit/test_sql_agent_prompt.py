from app.agents.sql_agent import SYSTEM


def test_prompt_targets_duckdb_not_postgres():
    assert "DuckDB" in SYSTEM and "PostgreSQL" not in SYSTEM
    assert "data.<table>" in SYSTEM
    assert "::numeric" not in SYSTEM
