"""Visão Geral de Negócios (Gestão à Vista): leitura paginada e classificada dos indicadores.

Somente leitura. Reutiliza a autorização e o escopo de `business.py` (permissão `business.view`,
tenant+empresa via `tenant_connection`) e o avaliador central (`compute_state`/`evaluate`), sem
regra paralela. A classificação, a contagem por classe, a busca, o filtro, a ordenação e a
paginação acontecem aqui; o frontend apenas apresenta.
"""
from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from .auth import Identity, current_identity, tenant_header
from .business import _allowed_indicator_ids, _permissions, _require, _scope_allows
from .business_evaluation import compute_state
from .business_rules import evaluate, validate_thresholds
from .db import tenant_connection

router = APIRouter(prefix="/api/v1/business", tags=["business-overview"])

# Ordem padrão da grade (spec 4.1) e também a ordem dos totais devolvidos.
STATUS_ORDER = ("CRITICO", "ATENCAO", "DESATUALIZADO", "SEM_DADOS", "SEM_REGRA", "NORMAL")
StatusFilter = Literal["CRITICO", "ATENCAO", "DESATUALIZADO", "SEM_DADOS", "SEM_REGRA", "NORMAL"]
DIRECTION_API = {"above": "MAIOR_PIORA", "below": "MENOR_PIORA"}
FUTURE_TOLERANCE = timedelta(minutes=5)  # mesma tolerância de business_rules.evaluate


def decimal_text(value: Decimal | None) -> str | None:
    """Decimal em texto (nunca float): sem zeros à direita além de 2 casas, sem notação científica."""
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." not in text:
        return text + ".00"
    whole, frac = text.split(".")
    frac = frac.rstrip("0")
    return f"{whole}.{frac.ljust(2, '0')}"


def _duration_text(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds < 3600:
        return f"{max(seconds // 60, 1)} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def rule_problem(direction: str | None, warning: Decimal | None, critical: Decimal | None) -> str | None:
    """None quando a regra está completa e coerente; senão o motivo (nunca vira Normal)."""
    if direction not in DIRECTION_API:
        return "Direção da regra inválida."
    if warning is None and critical is None:
        return "Limites de alerta e crítico não configurados."
    if warning is None or critical is None:
        return "Regra incompleta: informe os limites de alerta e crítico."
    try:
        validate_thresholds(direction, warning, critical)
    except ValueError:
        return "Limites incoerentes com a direção da regra."
    return None


def effective_config(indicator: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    """Mesma escolha de `compute_state`: política ativa vence o cadastro do indicador."""
    if policy and policy.get("active"):
        return {**policy, "origem": "POLITICA"}
    return {**indicator, "origem": "INDICADOR"}


def classify(indicator: dict[str, Any], policy: dict[str, Any] | None, current: dict[str, Any] | None,
             now: datetime) -> dict[str, Any]:
    """Classe principal, mutuamente exclusiva, de um indicador ativo.

    `current` é a linha de cm_business_states (last_value, last_collected_at, state, evaluated_at,
    state_changed_at). Retorna qualidade da coleta, severidade (só quando a leitura é válida e há
    regra), statusPrincipal e o motivo exibível.
    """
    config = effective_config(indicator, policy)
    direction, warning, critical = config.get("direction"), config.get("warning_value"), config.get("critical_value")
    value = current.get("last_value") if current else None
    collected = current.get("last_collected_at") if current else None
    stale_after = int(config["stale_after_seconds"])
    problem = rule_problem(direction, warning, critical)
    out: dict[str, Any] = {"qualidade": "VALIDA", "severidade": None, "motivo": None, "problema_regra": problem}

    if value is None or collected is None:
        out.update(qualidade="SEM_DADOS", motivo="Nenhuma coleta válida registrada para este indicador.")
    elif collected > now + FUTURE_TOLERANCE:
        out.update(qualidade="SEM_DADOS", motivo="A coleta tem data futura e foi desconsiderada.")
    elif now - collected > timedelta(seconds=stale_after):
        out.update(qualidade="DESATUALIZADA",
                   motivo=f"Última coleta há {_duration_text(now - collected)}, fora do prazo de validade de "
                          f"{_duration_text(timedelta(seconds=stale_after))} deste indicador.")
    elif problem:
        out.update(qualidade="SEM_REGRA", motivo=problem)
    else:
        state = compute_state(indicator, current, policy, now)
        if state not in ("normal", "atencao", "critico"):
            # Fora da janela de avaliação sem estado prévio válido: usa a comparação direta, igual ao avaliador.
            state = evaluate(value, collected, now=now, stale_after_seconds=stale_after,
                             direction=direction, warning=warning, critical=critical)
        out["severidade"] = {"normal": "NORMAL", "atencao": "ATENCAO", "critico": "CRITICO"}.get(state)
        if out["severidade"] is None:  # defensivo: nunca exibir saudável sem avaliação
            out.update(qualidade="SEM_DADOS", motivo="Indicador sem avaliação válida.")

    status = {"SEM_DADOS": "SEM_DADOS", "DESATUALIZADA": "DESATUALIZADO", "SEM_REGRA": "SEM_REGRA"}.get(
        out["qualidade"], out["severidade"])
    out["statusPrincipal"] = status
    return out


def build_item(row: dict[str, Any], policy: dict[str, Any] | None, current: dict[str, Any] | None,
               last_known: dict[str, Any] | None, now: datetime, actions: list[str]) -> dict[str, Any]:
    result = classify(row, policy, current, now)
    config = effective_config(row, policy)
    value = current.get("last_value") if current else None
    collected = current.get("last_collected_at") if current else None
    if value is None and last_known:  # série histórica: após revisão de regra o estado é zerado
        value, collected = last_known["value"], last_known["collected_at"]
    shows_value = result["qualidade"] in ("VALIDA", "SEM_REGRA")
    last_evaluated = current.get("evaluated_at") if current else None
    return {
        "id": str(row["id"]), "codigo": row["code"], "nome": row["name"], "descricao": row.get("description") or "",
        "unidade": row["unit"], "direcao": DIRECTION_API.get(config.get("direction")),
        "limiteAlerta": decimal_text(config.get("warning_value")),
        "limiteCritico": decimal_text(config.get("critical_value")),
        "versaoRegra": config.get("version"), "origemRegra": config["origem"],
        "configurada": result["problema_regra"] is None,
        "validadeColetaSegundos": int(config["stale_after_seconds"]),
        "valorAtual": decimal_text(value) if shows_value else None,
        "ultimoValorConhecido": decimal_text(value),
        "coletadoEm": collected.isoformat() if collected else None,
        "avaliadoEm": last_evaluated.isoformat() if last_evaluated else None,
        "fonte": (last_known or {}).get("source_name"),
        "qualidadeColeta": result["qualidade"], "motivoQualidade": result["motivo"],
        "severidade": result["severidade"], "statusPrincipal": result["statusPrincipal"],
        "estadoPersistido": (current or {}).get("state"),
        "acoesPermitidas": actions,
    }


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if not unicodedata.combining(c))


def matches_search(item: dict[str, Any], term: str) -> bool:
    term = _fold(term.strip())
    return not term or term in _fold(f"{item['nome']} {item['codigo']}")


def sort_key(item: dict[str, Any]) -> tuple:
    """Classe (Crítico → Normal), coleta mais recente, nome e, por fim, id (desempate determinístico)."""
    collected = datetime.fromisoformat(item["coletadoEm"]).timestamp() if item["coletadoEm"] else None
    return (STATUS_ORDER.index(item["statusPrincipal"]),
            0 if collected is not None else 1, -(collected or 0), _fold(item["nome"]), item["nome"], item["id"])


def totals(items: list[dict[str, Any]]) -> dict[str, int]:
    count = {status: 0 for status in STATUS_ORDER}
    for item in items:
        count[item["statusPrincipal"]] += 1
    return {"total": len(items), **count}


def build_overview(items: list[dict[str, Any]], *, q: str = "", status: str | None = None,
                   page: int = 1, page_size: int = 10) -> dict[str, Any]:
    """Totais sobre TODOS os indicadores autorizados; busca/filtro/página só refinam `itens`."""
    global_totals = totals(items)
    visible = sorted((i for i in items if matches_search(i, q) and (not status or i["statusPrincipal"] == status)),
                     key=sort_key)
    pages = max((len(visible) + page_size - 1) // page_size, 1)
    page = min(page, pages)
    start = (page - 1) * page_size
    return {"totais": global_totals, "totalFiltrado": len(visible), "pagina": page, "tamanhoPagina": page_size,
            "totalPaginas": pages, "itens": visible[start:start + page_size]}


@router.get("/overview")
def business_overview(q: str = Query("", max_length=120), status: StatusFilter | None = None,
                      page: int = Query(1, ge=1), page_size: Literal[10, 25, 50] = 10,
                      tenant_id: str = Depends(tenant_header), identity: Identity = Depends(current_identity)):
    actor, scope = _require(identity, tenant_id, "business.view")
    now = datetime.now(UTC)
    with tenant_connection(tenant_id) as db:
        ids = _allowed_indicator_ids(db, tenant_id, actor, scope)
        perms = _permissions(db, tenant_id, actor["id"])
        rows = db.execute("""SELECT i.*,
              s.state AS s_state,s.last_value AS s_last_value,s.last_collected_at AS s_last_collected_at,
              s.evaluated_at AS s_evaluated_at,s.state_changed_at AS s_state_changed_at,
              p.id AS p_id,p.active AS p_active,p.version AS p_version,p.warning_value AS p_warning_value,
              p.critical_value AS p_critical_value,p.direction AS p_direction,p.hysteresis AS p_hysteresis,
              p.cooldown_seconds AS p_cooldown_seconds,p.timezone AS p_timezone,p.weekdays AS p_weekdays,
              p.check_from AS p_check_from,p.check_until AS p_check_until,p.stale_after_seconds AS p_stale_after_seconds,
              v.value AS v_value,v.collected_at AS v_collected_at,d.name AS v_source_name
            FROM cm_business_indicators i
            LEFT JOIN cm_business_states s ON s.tenant_id=i.tenant_id AND s.indicator_id=i.id
            LEFT JOIN cm_business_rule_policies p ON p.tenant_id=i.tenant_id AND p.indicator_id=i.id
            LEFT JOIN LATERAL (SELECT value,collected_at,collector_id FROM cm_business_values
                 WHERE tenant_id=i.tenant_id AND indicator_id=i.id ORDER BY collected_at DESC LIMIT 1) v ON TRUE
            LEFT JOIN cm_business_collector_defs d ON d.tenant_id=i.tenant_id AND d.id=v.collector_id
            WHERE i.tenant_id=%s AND i.id=ANY(%s::uuid[])""", (tenant_id, list(ids))).fetchall() if ids else []
    update_scope = perms.get("business.monitor.update")
    items, inactive, evaluated = [], 0, []
    for row in rows:
        if not row["active"]:
            inactive += 1  # suspenso não é uma das 6 classes: fica fora dos totais e é informado à parte
            continue
        if row["s_evaluated_at"]:
            evaluated.append(row["s_evaluated_at"])
        policy = {k[2:]: row[k] for k in row if k.startswith("p_")} if row["p_id"] else None
        current = {k[2:]: row[k] for k in row if k.startswith("s_")} if row["s_state"] is not None else None
        if current and current.get("state_changed_at") is None:
            current["state_changed_at"] = current.get("evaluated_at")
        last_known = {"value": row["v_value"], "collected_at": row["v_collected_at"],
                      "source_name": row["v_source_name"]} if row["v_collected_at"] else None
        actions = ["VISUALIZAR"]
        if update_scope and _scope_allows(row, actor, update_scope):
            actions.append("EDITAR")
        items.append(build_item(row, policy, current, last_known, now, actions))
    return {"empresaId": tenant_id, "consultadoEm": now.isoformat(),
            "ultimaAvaliacaoEm": max(evaluated).isoformat() if evaluated else None, "indicadoresSuspensos": inactive,
            "ordemPadrao": list(STATUS_ORDER), **build_overview(items, q=q, status=status, page=page, page_size=page_size)}
