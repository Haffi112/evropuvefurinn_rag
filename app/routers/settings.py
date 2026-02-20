import logging
import re

from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import verify_api_key
from app.models.schemas import SettingUpdate
from app.services import settings_service

_MODEL_NAME_RE = re.compile(r"^gemini-[\w][\w.\-]{2,80}$")

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/settings",
    tags=["admin"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/", summary="List all settings")
async def list_settings():
    return {"settings": settings_service.get_all()}


@router.put("/{key:path}", summary="Update a setting")
async def update_setting(key: str, body: SettingUpdate):
    try:
        meta = settings_service._registry.get(key)
        if not meta:
            raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")

        # Validate numeric fields
        if meta.input_type == "number":
            try:
                float(body.value)
            except ValueError:
                raise HTTPException(
                    status_code=422,
                    detail=f"Setting '{key}' requires a numeric value",
                )

        # Validate model name fields
        if key in ("model.pro_name", "model.flash_name"):
            if not _MODEL_NAME_RE.match(body.value):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Invalid model name '{body.value}'. "
                        "Must match pattern: gemini-<name> "
                        "(letters, digits, dots, hyphens; 3-81 chars after 'gemini-')"
                    ),
                )

        await settings_service.set_value(key, body.value)
        return {"key": key, "value": body.value}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{key:path}", summary="Reset a setting to default")
async def delete_setting(key: str):
    try:
        await settings_service.delete_override(key)
        meta = settings_service._registry[key]
        return {"key": key, "value": meta.default, "reset": True}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
