import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _rate_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        try:
            payload = jwt.decode(header[7:], request.app.state.settings.jwt_secret, algorithms=["HS256"])
            return f"user:{payload['sub']}"
        except Exception:  # noqa: BLE001 - fall back to IP for bad tokens
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=_rate_key)


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429, content={"error": {"code": "rate_limited", "message": f"Too many requests ({exc.detail})."}}
    )
