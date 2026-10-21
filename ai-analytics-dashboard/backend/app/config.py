from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://analytics:analytics@postgres:5432/analytics"
    query_ro_url: str = "postgresql://query_ro:query_ro@postgres:5432/analytics"
    redis_url: str = "redis://redis:6379/0"

    llm_provider: str = "gemini"  # "gemini" | "openai" | "fake"

    # ---- Gemini (active) ----
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash-lite"
    gemini_planner_model: str = "gemini-2.5-flash-lite"
    gemini_embed_model: str = "gemini-embedding-2"
    gemini_temperature: float | None = 0.0
    gemini_timeout_s: float = 20

    # ---- OpenAI (switch back: uncomment these, the openai_* properties below, and the block in llm/provider.py) ----
    # openai_api_key: str = ""
    # openai_chat_model: str = "gpt-4o-mini"
    # openai_planner_model: str = "gpt-4o"
    # openai_embed_model: str = "text-embedding-3-small"
    # openai_temperature: float | None = 0.0
    # openai_timeout_s: float = 20

    # Must match the pgvector column (db/models.py EMBED_DIM). Gemini embeddings are truncated to this size.
    embed_dim: int = 1536

    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 60
    admin_email: str = "admin@analytics.local"
    admin_password: str = "Test@123"

    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "AI Analytics <no-reply@analytics.local>"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False

    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 50
    max_sql_rows: int = 5000
    step_timeout_s: int = 30
    query_timeout_s: float = 150  # must stay below nginx proxy_read_timeout (180 s)
    cache_ttl_s: int = 3600
    rate_limit_enabled: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Provider-neutral names used by the agents. Point these at the active provider's fields.
    @property
    def planner_model(self) -> str:
        return self.gemini_planner_model
        # return self.openai_planner_model

    @property
    def embed_model_name(self) -> str:
        return self.gemini_embed_model
        # return self.openai_embed_model

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
