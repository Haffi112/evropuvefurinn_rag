from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from app.config import get_settings

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    description="API key for protected endpoints (article writes, stats, admin).",
)


async def verify_api_key(x_api_key: str = Depends(_api_key_header)) -> str:
    if x_api_key != get_settings().cms_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key
