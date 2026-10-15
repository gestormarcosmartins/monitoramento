"""Avisos de chamado contra um PostgreSQL real (opt-in, mesmo padrão de test_integration_postgres.py).

Cobre: aviso sem destinatário fica visível no chamado; rótulos corretos (FALHA/NORMALIZADO) e que reconhecimento/SLA
não são enviados à equipe como se fossem recuperação.
"""
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
class NoRecipientFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.migrate()
        with connect() as c:
            ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
            cls.tenant = str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,%s,%s) RETURNING id",
                                       (ws, "Empresa Avisos", "avisos-" + uuid.uuid4().hex[:6])).fetchone()["id"])
        with tenant_connection(cls.tenant) as c:
            cls.asset = c.execute("INSERT INTO cm_assets(tenant_id,name,kind) VALUES (%s,'srv','website') RETURNING id", (cls.tenant,)).fetchone()["id"]

    def incident(self, team_emails=None):
        """Cria monitor HTTP (abre na hora), opcionalmente com equipe responsável, e devolve (incident_id, monitor)."""
        with tenant_connection(self.tenant) as c:
            team_id = None
            if team_emails:
                team_id = c.execute("INSERT INTO cm_teams(tenant_id,name,emails) VALUES (%s,%s,%s) RETURNING id",
                                    (self.tenant, "eq-" + uuid.uuid4().hex[:6], team_emails)).fetchone()["id"]
            monitor = dict(c.execute(
                """INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,severity,responsible_team_id)
                VALUES (%s,%s,%s,'http','{}'::jsonb,'high',%s) RETURNING *""",
                (self.tenant, self.asset, "m-" + uuid.uuid4().hex[:8], team_id)).fetchone())
        worker.record_result(monitor, False, "falha")
        with tenant_connection(self.tenant) as c:
            row = c.execute("SELECT i.id FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id WHERE a.monitor_id=%s", (monitor["id"],)).fetchone()
        return str(row["id"]), monitor

    def drop_rules(self):
        with tenant_connection(self.tenant) as c:
            c.execute("DELETE FROM cm_notification_rules WHERE tenant_id=%s", (self.tenant,))

    def skipped(self, incident_id):
        with tenant_connection(self.tenant) as c:
            return c.execute("SELECT count(*) AS n FROM cm_incident_history WHERE incident_id=%s AND event_type='notification.skipped'", (incident_id,)).fetchone()["n"]

    def send(self, incident_id, event):
        with mock.patch.object(notifications, "send_email") as email, mock.patch.object(notifications, "send_whatsapp") as whats:
            notifications.deliver_notifications(self.tenant, incident_id, event, attempt=3)
        return email, whats

    def test_without_recipient_the_incident_shows_why_and_only_once(self):
        incident_id, _ = self.incident()
        email, whats = self.send(incident_id, "incident.created")
        self.send(incident_id, "incident.created")  # reprocessamento do outbox
        self.assertEqual(self.skipped(incident_id), 1)
        email.assert_not_called(); whats.assert_not_called()

    def test_with_team_email_it_sends_a_failure_message_and_records_no_skip(self):
        incident_id, _ = self.incident(team_emails=["equipe@t.test"])
        email, _ = self.send(incident_id, "incident.created")
        self.assertEqual(email.call_count, 1)
        self.assertEqual(email.call_args.args[1], "equipe@t.test")
        self.assertIn("ALERTA", email.call_args.args[2])
        self.assertEqual(self.skipped(incident_id), 0)

    def test_recovery_message_says_normalized(self):
        incident_id, _ = self.incident(team_emails=["equipe@t.test"])
        email, _ = self.send(incident_id, "incident.resolved")
        self.assertEqual(email.call_count, 1)
        self.assertIn("NORMALIZADO", email.call_args.args[2])

    def test_acknowledge_and_sla_events_are_not_sent_to_the_team_as_normalized(self):
        incident_id, _ = self.incident(team_emails=["equipe@t.test"])
        for event in ("incident.acknowledged", "incident.in_progress", "sla.resolution.level_1"):
            email, whats = self.send(incident_id, event)
            email.assert_not_called(); whats.assert_not_called()
        self.assertEqual(self.skipped(incident_id), 0)  # não se aplica: não é falta de destinatário

    def test_a_matching_rule_counts_as_a_recipient(self):
        incident_id, _ = self.incident()
        with tenant_connection(self.tenant) as c:
            c.execute("""INSERT INTO cm_notification_rules(tenant_id,name,channel,target,events,minimum_priority,active)
                VALUES (%s,'regra','email','regra@t.test',ARRAY['incident.created'],'low',true)""", (self.tenant,))
        self.addCleanup(self.drop_rules)  # regra vale para a empresa toda: não pode vazar para os outros testes
        email, _ = self.send(incident_id, "incident.created")
        self.assertEqual(email.call_count, 1)
        self.assertEqual(self.skipped(incident_id), 0)


if __name__ == "__main__":
    unittest.main()
