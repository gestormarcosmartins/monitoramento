from typing import Any

from fastapi import HTTPException


ACTIVE_FEATURE_MODES = frozenset({"write_pilot", "enabled"})


def feature_mode_allows(mode: str | None, *, default_enabled: bool = True) -> bool:
    """Return whether an explicitly configured rollout mode may serve user traffic."""
    if mode is None:
        return default_enabled
    return mode in ACTIVE_FEATURE_MODES


def require_feature(
    connection: Any,
    tenant_id: str,
    feature: str,
    *,
    default_enabled: bool = True,
) -> str:
    row = connection.execute(
        "SELECT mode FROM cm_feature_flags WHERE tenant_id=%s AND feature=%s",
        (tenant_id, feature),
    ).fetchone()
    mode = row["mode"] if row else None
    if not feature_mode_allows(mode, default_enabled=default_enabled):
        raise HTTPException(
            status_code=409,
            detail=f"Feature '{feature}' is not enabled for this company",
        )
    return mode or "enabled"
