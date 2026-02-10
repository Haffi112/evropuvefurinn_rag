from fastapi import Header, HTTPException

from app.config import get_settings


async def verify_api_key(x_api_key: str = Header(...)) -> str:
    if x_api_key != get_settings().cms_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key
