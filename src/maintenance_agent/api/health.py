from typing import Annotated

from fastapi import APIRouter, Depends

from maintenance_agent.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.app_env,
    }
