"""Padrão de notificações (Nexora_Padrao_Notificacoes_GLPI) contra um PostgreSQL real (opt-in)."""
import json
import os
import unittest
import uuid
from unittest import mock

URL = os.environ.get("NEXORA_TEST_DATABASE_URL")
if URL:
    os.environ.update({"CENTRAL_DATABASE_URL": URL, "REDIS_URL": "redis://localhost:6399/0", "CENTRAL_SECRET_KEY": "x" * 40,
                       "ZABBIX_API_URL": "http://z", "ZABBIX_API_TOKEN": "t"})
    from central_monitor import db, notifications, worker
    from central_monitor.db import connect, tenant_connection


@unittest.skipUnless(URL, "NEXORA_TEST_DATABASE_URL not set")
class StandardMessages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.migrate()
        with connect() as c:
            ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
            cls.tenant = str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,%s,%s) RETURNING id",
                                       (ws, "Cliente Padrao", "padrao-" + uuid.uuid4().hex[:6])).fetchone()["id"])
        with tenant_connection(cls.tenant) as c:
            cls.asset = c.execute("INSERT INTO cm_assets(tenant_id,name,kind) VALUES (%s,'srv','website') RETURNING id", (cls.tenant,)).fetchone()["id"]

    def make(self, monitor_type="http", ticket=None, sync="pending"):
        """Monitor com equipe (e-mail) responsável, em falha: devolve (incident_id, monitor)."""
        with tenant_connection(self.tenant) as c:
            team = c.execute("INSERT INTO cm_teams(tenant_id,name,emails) VALUES (%s,%s,%s) RETURNING id",
                             (self.tenant, "eq-" + uuid.uuid4().hex[:6], ["equipe@t.test"])).fetchone()["id"]
            monitor = dict(c.execute(
                """INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,severity,responsible_team_id)
                VALUES (%s,%s,%s,%s,'{}'::jsonb,'high',%s) RETURNING *""",
                (self.tenant, self.asset, "Servico " + uuid.uuid4().hex[:6], monitor_type, team)).fetchone())
        worker.record_result(monitor, False, "falha")
        if monitor_type == "push":
            with tenant_connection(self.tenant) as c:
                c.execute("UPDATE cm_monitors SET failing_since=now()-interval '11 minutes' WHERE id=%s", (monitor["id"],))
            worker.record_result(monitor, False, "falha")
        with tenant_connection(self.tenant) as c:
            row = c.execute("SELECT i.id FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id WHERE a.monitor_id=%s", (monitor["id"],)).fetchone()
            c.execute("UPDATE cm_incidents SET glpi_ticket_id=%s,sync_status=%s WHERE id=%s", (ticket, sync, row["id"]))
        return str(row["id"]), monitor

    def send(self, incident_id, event, attempt=0):
        with mock.patch.object(notifications, "send_email") as email, mock.patch.object(notifications, "send_whatsapp"):
            notifications.deliver_notifications(self.tenant, incident_id, event, attempt=attempt)
        return email

    def test_down_alert_has_customer_service_severity_start_and_ticket(self):
        incident_id, monitor = self.make(ticket=1360, sync="synced")
        email = self.send(incident_id, "incident.created")
        subject, body = email.call_args.args[2], email.call_args.args[3]
        self.assertIn("ALERTA CRÍTICO", subject)
        for line in ("🔴 🚨 NEXORA | ALERTA CRÍTICO", "Cliente: Cliente Padrao", f"Serviço/monitor: {monitor['name']}",
                     "Status: 🔴 INDISPONÍVEL", "Gravidade: 🚨 CRÍTICA", "Chamado GLPI: #1360"):
            self.assertIn(line, body)
        self.assertRegex(body, r"Início da falha: \d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} \(UTC-0[23]:00\)")

    def test_waits_for_the_ticket_number_then_says_in_opening(self):
        incident_id, _ = self.make(ticket=None, sync="pending")
        with self.assertRaises(RuntimeError):  # primeira tentativa: espera o GLPI gravar o número
            self.send(incident_id, "incident.created", attempt=0)
        email = self.send(incident_id, "incident.created", attempt=3)  # esgotou a espera: avisa sem inventar número
        self.assertIn("Chamado GLPI: Em abertura", email.call_args.args[3])

    def test_glpi_failure_still_alerts_with_explicit_text(self):
        incident_id, _ = self.make(ticket=None, sync="failed")
        email = self.send(incident_id, "incident.created", attempt=0)  # sem espera quando a integração já falhou
        self.assertIn("Chamado GLPI: não gerado (falha de integração)", email.call_args.args[3])

    def test_normalization_keeps_same_ticket_original_severity_and_computed_duration(self):
        incident_id, monitor = self.make(ticket=1361, sync="synced")
        worker.record_result(monitor, True, "ok")
        with tenant_connection(self.tenant) as c:  # início exatamente 7 min 20 s antes da normalização
            c.execute("""UPDATE cm_alerts a SET failure_started_at=i.resolved_at-interval '7 minutes 20 seconds'
                FROM cm_incidents i WHERE i.alert_id=a.id AND i.id=%s""", (incident_id,))
        email = self.send(incident_id, "incident.resolved", attempt=0)
        body = email.call_args.args[3]
        for line in ("🟢 ✅ NEXORA | SERVIÇO NORMALIZADO", "Cliente: Cliente Padrao", "Status: 🟢 NORMALIZADO",
                     "Gravidade original: 🚨 CRÍTICA", "Duração: 7 min 20 s", "Chamado GLPI: #1361"):
            self.assertIn(line, body)
        self.assertIn("Normalizado em:", body)

    def test_start_of_failure_is_the_real_start_even_with_the_grace_period(self):
        incident_id, monitor = self.make("push", ticket=1362, sync="synced")
        with tenant_connection(self.tenant) as c:
            row = c.execute("SELECT (extract(epoch FROM (a.opened_at - a.failure_started_at))) AS atraso FROM cm_alerts a WHERE a.monitor_id=%s", (monitor["id"],)).fetchone()
        self.assertTrue(600 <= float(row["atraso"]) <= 720, row["atraso"])  # ~11 min de falha antes de abrir o chamado

    def test_same_event_is_not_sent_twice(self):
        incident_id, _ = self.make(ticket=1363, sync="synced")
        first = self.send(incident_id, "incident.created")
        second = self.send(incident_id, "incident.created")  # reprocessamento do outbox
        self.assertEqual(first.call_count, 1)
        self.assertEqual(second.call_count, 0)

    def test_webhook_payload_is_json_serializable_and_carries_the_standard_fields(self):
        incident_id, monitor = self.make(ticket=1364, sync="synced")
        with tenant_connection(self.tenant) as c:
            c.execute("""INSERT INTO cm_notification_rules(tenant_id,name,channel,target,events,minimum_priority,active)
                VALUES (%s,'hook','webhook','https://hook.t.test/x',ARRAY['incident.created'],'low',true)""", (self.tenant,))
        self.addCleanup(lambda: self._drop_rules())
        with mock.patch.object(notifications, "send_email"), mock.patch.object(notifications, "send_whatsapp"),                 mock.patch.object(notifications.httpx, "post") as post:
            notifications.deliver_notifications(self.tenant, incident_id, "incident.created", attempt=0)
        body = post.call_args.kwargs["json"]
        json.dumps(body)  # não pode levantar: datas viram texto ISO
        self.assertEqual((body["cliente"], body["monitor"], body["glpi_chamado_numero"]), ("Cliente Padrao", monitor["name"], 1364))
        self.assertIn("NEXORA | ALERTA CRÍTICO", body["mensagem"])
        self.assertRegex(body["inicio_ocorrencia"], r"^\d{4}-\d{2}-\d{2}T")

    def _drop_rules(self):
        with tenant_connection(self.tenant) as c:
            c.execute("DELETE FROM cm_notification_rules WHERE tenant_id=%s", (self.tenant,))


if __name__ == "__main__":
    unittest.main()
