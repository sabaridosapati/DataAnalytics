from app.config import Settings


def test_settings_read_env(monkeypatch):
    monkeypatch.setenv("MAX_SQL_ROWS", "123")
    s = Settings()
    assert s.max_sql_rows == 123


def test_cors_origin_list_default():
    s = Settings(cors_origins="http://localhost:3000, http://localhost:5173 ,")
    assert s.cors_origin_list == ["http://localhost:3000", "http://localhost:5173"]


def test_test_env_uses_fake_llm():
    assert Settings().llm_provider == "fake"
