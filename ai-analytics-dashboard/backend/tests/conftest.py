"""Global test setup. Executed before any `app` module is imported."""
import os
import socket
import tempfile
from pathlib import Path

import pytest

# ---- zero-cost, isolated test environment ----
os.environ["LLM_PROVIDER"] = "fake"
os.environ["GEMINI_API_KEY"] = ""
# os.environ["OPENAI_API_KEY"] = ""
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["UPLOAD_DIR"] = "/tmp/test_uploads"
os.environ["DUCKDB_DIR"] = os.path.join(tempfile.gettempdir(), "test_duckdb")
if os.environ.get("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
if os.environ.get("TEST_QUERY_RO_URL"):
    os.environ["QUERY_RO_URL"] = os.environ["TEST_QUERY_RO_URL"]
_redis = os.environ.get("REDIS_URL", "redis://redis:6379/0")
os.environ["REDIS_URL"] = _redis.rsplit("/", 1)[0] + "/1"

# ---- network guard: tests must never reach OpenAI ----
# Only the opt-in live suite (LIVE_OPENAI_API_KEY set explicitly by the user) lifts it.
_real_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host and ("googleapis.com" in str(host) or "openai.com" in str(host)):
        raise RuntimeError("Tests must not call the Gemini/OpenAI API (zero-cost policy)")
    return _real_getaddrinfo(host, *args, **kwargs)


if not os.environ.get("LIVE_OPENAI_API_KEY"):
    socket.getaddrinfo = _guarded_getaddrinfo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory) -> Path:
    """Committed fixtures if present, otherwise generate them into a temp dir."""
    if (FIXTURES / "expected_answers.json").exists():
        return FIXTURES
    from scripts.generate_test_data import generate

    out = tmp_path_factory.mktemp("fixtures")
    generate(out)
    return out
