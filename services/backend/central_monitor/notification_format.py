"""Padrão oficial das mensagens de incidente e normalização (Nexora_Padrao_Notificacoes_GLPI v1.0).

Nunca fabrica horário, duração ou número de chamado: o que não existe aparece como estado explícito.
"""
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# prioridade do incidente -> (ícone do cabeçalho, ícone da gravidade, gravidade, adjetivo do "ALERTA ...")
SEVERITY_VIEW = {
    "critical": ("🔴", "🚨", "CRÍTICA", "CRÍTICO"),
    "high": ("🟠", "⚠️", "ALTA", "ALTO"),
    "medium": ("🟡", "⚠️", "MÉDIA", "MÉDIO"),
    "low": ("🔵", "ℹ️", "BAIXA", "BAIXO"),
}
UPDATE_LABELS = {
    "incident.acknowledged": "Reconhecido",
    "incident.in_progress": "Em andamento",
    "incident.waiting_customer": "Aguardando cliente",
    "incident.waiting_supplier": "Aguardando fornecedor",
    "incident.closed": "Fechado",
}
GLPI_WAIT_ATTEMPTS = 3  # tentativas curtas (~14 s) esperando o número do chamado antes de enviar "Em abertura"


def severity_view(priority: str | None) -> tuple[str, str, str, str]:
    return SEVERITY_VIEW.get(priority or "", SEVERITY_VIEW["medium"])


def format_local(moment: datetime | None, timezone: str | None) -> str:
    if moment is None:
        return "não disponível"
    try:
        local = moment.astimezone(ZoneInfo(timezone or "America/Sao_Paulo"))
    except Exception:
        local = moment
    offset = local.strftime("%z") or "+0000"
    return f"{local:%d/%m/%Y %H:%M:%S} (UTC{offset[:3]}:{offset[3:]})"


def format_duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None or end < start:
        return "não disponível"
    seconds = int((end - start).total_seconds())
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days} d {hours:02d} h {minutes:02d} min"
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def glpi_text(ticket_id: Any, sync_status: str | None) -> str:
    if ticket_id:
        return f"#{ticket_id}"
    if sync_status == "failed":
        return "não gerado (falha de integração)"
    return "Em abertura"


def must_wait_for_glpi(event: str, ctx: dict[str, Any], attempt: int) -> bool:
    """Chamado novo, número ainda não gravado e integração sem falha: vale esperar um pouco antes de avisar."""
    return (event == "incident.created" and not ctx.get("glpi_ticket_id")
            and ctx.get("sync_status") == "pending" and attempt < GLPI_WAIT_ATTEMPTS)


def load_context(connection: Any, tenant_id: str, incident_id: str) -> dict[str, Any] | None:
    return connection.execute(
        """SELECT i.id,i.title,i.status,i.priority,i.created_at,i.resolved_at,i.glpi_ticket_id,i.sync_status,
            t.name AS cliente,t.timezone,m.name AS monitor,
            COALESCE(a.failure_started_at,a.opened_at,i.created_at) AS inicio
        FROM cm_incidents i JOIN cm_tenants t ON t.id=i.tenant_id
        LEFT JOIN cm_alerts a ON a.id=i.alert_id LEFT JOIN cm_monitors m ON m.id=a.monitor_id
        WHERE i.tenant_id=%s AND i.id=%s""", (tenant_id, incident_id)).fetchone()


def build_notification(event: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """Devolve (assunto do e-mail, texto). Queda, normalização e atualização seguem os modelos do documento."""
    head_icon, sev_icon, sev_name, sev_adj = severity_view(ctx.get("priority"))
    cliente = ctx.get("cliente") or "não informado"
    monitor = ctx.get("monitor") or ctx.get("title") or "não informado"
    inicio = format_local(ctx.get("inicio"), ctx.get("timezone"))
    glpi = glpi_text(ctx.get("glpi_ticket_id"), ctx.get("sync_status"))
    common = [f"Cliente: {cliente}", f"Serviço/monitor: {monitor}"]
    if event == "incident.resolved":
        fim = ctx.get("resolved_at")
        lines = [f"🟢 ✅ NEXORA | SERVIÇO NORMALIZADO", *common, "Status: 🟢 NORMALIZADO",
                 f"Gravidade original: {sev_icon} {sev_name}", f"Início da falha: {inicio}",
                 f"Normalizado em: {format_local(fim, ctx.get('timezone'))}",
                 f"Duração: {format_duration(ctx.get('inicio'), fim)}", f"Chamado GLPI: {glpi}"]
        subject = f"Nexora | SERVIÇO NORMALIZADO | {cliente} | {monitor}"
    elif event == "incident.created":
        lines = [f"{head_icon} {sev_icon} NEXORA | ALERTA {sev_adj}", *common, "Status: 🔴 INDISPONÍVEL",
                 f"Gravidade: {sev_icon} {sev_name}", f"Início da falha: {inicio}", f"Chamado GLPI: {glpi}"]
        subject = f"Nexora | ALERTA {sev_adj} | {cliente} | {monitor}"
    else:
        label = UPDATE_LABELS.get(event) or ("Prazo de SLA" if event.startswith("sla.") else event.removeprefix("incident."))
        lines = ["🔔 NEXORA | ATUALIZAÇÃO DO CHAMADO", *common, f"Situação: {label}",
                 f"Gravidade: {sev_icon} {sev_name}", f"Início da falha: {inicio}", f"Chamado GLPI: {glpi}"]
        subject = f"Nexora | ATUALIZAÇÃO | {cliente} | {monitor}"
    return subject, "\n".join(lines)
