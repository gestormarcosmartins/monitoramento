from typing import Any


def incident_scope(actor: dict[str, Any], alias: str = "i") -> tuple[str, list[Any]]:
    """Build the mandatory row predicate for an incident/ticket query.

    Tenant isolation remains enforced independently by RLS. This predicate narrows
    rows inside the selected company according to the active access grant.
    """
    if actor.get("is_master") or actor.get("role") == "master" or actor.get("row_scope") == "company":
        return "TRUE", []
    user_id = actor.get("id")
    scope = actor.get("row_scope") or "own"
    if scope == "own":
        return f"({alias}.requester_user_id=%s OR {alias}.assignee_user_id=%s)", [user_id, user_id]
    if scope == "department":
        department_id = actor.get("department_id")
        if not department_id:
            return "FALSE", []
        return (
            f"EXISTS (SELECT 1 FROM cm_user_departments scoped_department "
            f"WHERE scoped_department.tenant_id={alias}.tenant_id "
            f"AND scoped_department.user_id={alias}.requester_user_id "
            "AND scoped_department.department_id=%s)",
            [department_id],
        )
    if scope == "team_service":
        predicates: list[str] = []
        params: list[Any] = []
        if actor.get("team_id"):
            predicates.append(f"{alias}.assignee_team_id=%s")
            params.append(actor["team_id"])
        if actor.get("engagement_id"):
            predicates.append(f"{alias}.engagement_id=%s")
            params.append(actor["engagement_id"])
        return (f"({' OR '.join(predicates)})", params) if predicates else ("FALSE", [])
    return "FALSE", []
