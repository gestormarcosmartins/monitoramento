"""Carência de falha: evita abrir alerta/chamado por quedas curtas que se recuperam sozinhas."""
from typing import Any

# Monitores alimentados por agente (tipo 'push') recebem resultados em lote e oscilam em coletas pesadas;
# os demais (HTTP/TCP do worker) continuam abrindo na hora, salvo configuração explícita.
GRACE_BY_DEFAULT_TYPES = ("push",)


def effective_grace_seconds(monitor: dict[str, Any], push_default: int) -> int:
    configured = monitor.get("failure_grace_seconds")
    if configured is not None:
        return max(0, int(configured))
    return max(0, int(push_default)) if monitor.get("monitor_type") in GRACE_BY_DEFAULT_TYPES else 0


def must_wait(failing_seconds: float | None, grace_seconds: int, alert_already_open: bool) -> bool:
    """True = ainda em carência: não criar alerta nem chamado agora.
    Um alerta já aberto nunca volta para a carência (continua atualizando a mensagem)."""
    if grace_seconds <= 0 or alert_already_open:
        return False
    return float(failing_seconds or 0) < grace_seconds
