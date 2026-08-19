from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession


class ConsequentialActionGuardError(RuntimeError):
    pass


class SubmitWorkOrderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_id: str
    status: Literal["submitted"]


async def submit_work_order(
    draft_id: str,
    *,
    approval_status: str,
    session: AsyncSession,
) -> SubmitWorkOrderResult:
    del session
    if approval_status != "approved":
        raise ConsequentialActionGuardError(
            "submit_work_order requires approval_status='approved'."
        )
    raise NotImplementedError("submit_work_order is reserved for the Phase 6 resume path.")
