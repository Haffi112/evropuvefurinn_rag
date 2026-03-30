from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(
    description="API key for protected endpoints (article writes, stats, admin).",
)


async def verify_api_key(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> str:
    if creds.credentials != get_settings().cms_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return creds.credentials
