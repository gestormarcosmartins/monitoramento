from enum import StrEnum

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(title="Central Monitor Action Orchestrator", version="0.2.0")


class ActionType(StrEnum):
    RERUN_IDEMPOTENT_JOB = "rerun_idempotent_job"
    RESTART_STATELESS_WORKER = "restart_stateless_worker"
    COLLECT_DIAGNOSTICS = "collect_diagnostics"


class ActionRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    action: ActionType
    idempotency_key: str = Field(min_length=16, max_length=128)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "up"}


@app.post("/internal/actions/validate", tags=["actions"])
def validate_action(request: ActionRequest) -> dict[str, str]:
    # The public API persists grants, cooldown, rate limit and audit. This internal
    # boundary intentionally accepts only the closed catalog and never a command.
    if request.action not in ActionType:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return {
        "status": "validated_not_executed",
        "action": request.action.value,
        "idempotency_key": request.idempotency_key,
    }

