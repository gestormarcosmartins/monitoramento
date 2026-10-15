import unittest
from datetime import UTC, datetime, timedelta

from central_monitor.notification_format import (build_notification, format_duration, format_local, glpi_text,
                                                 must_wait_for_glpi, severity_view)

START = datetime(2026, 10, 15, 14, 44, 27, tzinfo=UTC)
CTX = {"cliente": "Morada", "monitor": "Banco de Dados 19 - Bloqueador-raiz", "priority": "critical", "title": "x",
       "timezone": "America/Sao_Paulo", "inicio": START, "resolved_at": None, "glpi_ticket_id": 1360, "sync_status": "synced"}


class Format(unittest.TestCase):
    def test_down_alert_matches_the_official_model(self):
        subject, text = build_notification("incident.created", CTX)
        self.assertEqual(text.split("\n"), [
            "🔴 🚨 NEXORA | ALERTA CRÍTICO",
            "Cliente: Morada",
            "Serviço/monitor: Banco de Dados 19 - Bloqueador-raiz",
            "Status: 🔴 INDISPONÍVEL",
            "Gravidade: 🚨 CRÍTICA",
            "Início da falha: 15/10/2026 11:44:27 (UTC-03:00)",
            "Chamado GLPI: #1360",
        ])
        self.assertEqual(subject, "Nexora | ALERTA CRÍTICO | Morada | Banco de Dados 19 - Bloqueador-raiz")

    def test_normalized_matches_the_official_model_and_keeps_original_severity(self):
        ctx = {**CTX, "resolved_at": START + timedelta(minutes=7, seconds=20)}
        subject, text = build_notification("incident.resolved", ctx)
        self.assertEqual(text.split("\n"), [
            "🟢 ✅ NEXORA | SERVIÇO NORMALIZADO",
            "Cliente: Morada",
            "Serviço/monitor: Banco de Dados 19 - Bloqueador-raiz",
            "Status: 🟢 NORMALIZADO",
            "Gravidade original: 🚨 CRÍTICA",
            "Início da falha: 15/10/2026 11:44:27 (UTC-03:00)",
            "Normalizado em: 15/10/2026 11:51:47 (UTC-03:00)",
            "Duração: 7 min 20 s",
            "Chamado GLPI: #1360",
        ])
        self.assertTrue(subject.startswith("Nexora | SERVIÇO NORMALIZADO"))

    def test_severity_icons_and_names_follow_the_table(self):
        self.assertEqual(severity_view("critical")[:3], ("🔴", "🚨", "CRÍTICA"))
        self.assertEqual(severity_view("high")[:3], ("🟠", "⚠️", "ALTA"))
        self.assertEqual(severity_view("medium")[:3], ("🟡", "⚠️", "MÉDIA"))
        self.assertEqual(severity_view("low")[:3], ("🔵", "ℹ️", "BAIXA"))
        _, text = build_notification("incident.created", {**CTX, "priority": "high"})
        self.assertTrue(text.startswith("🟠 ⚠️ NEXORA | ALERTA ALTO"))

    def test_glpi_states_are_explicit_and_never_invented(self):
        self.assertEqual(glpi_text(1360, "synced"), "#1360")
        self.assertEqual(glpi_text(None, "pending"), "Em abertura")
        self.assertEqual(glpi_text(None, "failed"), "não gerado (falha de integração)")

    def test_missing_times_are_not_fabricated(self):
        _, text = build_notification("incident.resolved", {**CTX, "resolved_at": None})
        self.assertIn("Normalizado em: não disponível", text)
        self.assertIn("Duração: não disponível", text)
        self.assertEqual(format_duration(START, None), "não disponível")
        self.assertEqual(format_duration(START + timedelta(seconds=5), START), "não disponível")  # fim antes do início
        self.assertEqual(format_local(None, "America/Sao_Paulo"), "não disponível")

    def test_duration_formats(self):
        self.assertEqual(format_duration(START, START + timedelta(seconds=45)), "45 s")
        self.assertEqual(format_duration(START, START + timedelta(minutes=7, seconds=20)), "7 min 20 s")
        self.assertEqual(format_duration(START, START + timedelta(hours=1, minutes=5)), "1 h 05 min")
        self.assertEqual(format_duration(START, START + timedelta(days=2, hours=3, minutes=4)), "2 d 03 h 04 min")

    def test_uses_the_customer_timezone(self):
        self.assertEqual(format_local(START, "America/Sao_Paulo"), "15/10/2026 11:44:27 (UTC-03:00)")
        self.assertEqual(format_local(START, "UTC"), "15/10/2026 14:44:27 (UTC+00:00)")

    def test_other_events_use_the_update_template_without_normalization_wording(self):
        _, text = build_notification("incident.in_progress", CTX)
        self.assertIn("ATUALIZAÇÃO DO CHAMADO", text)
        self.assertIn("Situação: Em andamento", text)
        self.assertNotIn("NORMALIZADO", text)

    def test_waits_for_the_ticket_number_only_for_new_incidents_with_pending_sync(self):
        pending = {**CTX, "glpi_ticket_id": None, "sync_status": "pending"}
        self.assertTrue(must_wait_for_glpi("incident.created", pending, 0))
        self.assertTrue(must_wait_for_glpi("incident.created", pending, 2))
        self.assertFalse(must_wait_for_glpi("incident.created", pending, 3))  # chega de esperar: "Em abertura"
        self.assertFalse(must_wait_for_glpi("incident.created", {**pending, "sync_status": "failed"}, 0))
        self.assertFalse(must_wait_for_glpi("incident.created", CTX, 0))
        self.assertFalse(must_wait_for_glpi("incident.resolved", pending, 0))


if __name__ == "__main__":
    unittest.main()
