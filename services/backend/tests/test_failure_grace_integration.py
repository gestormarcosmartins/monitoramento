"""Carência de falha contra um PostgreSQL real (opt-in, mesmo padrão de test_integration_postgres.py).

Defina NEXORA_TEST_DATABASE_URL para um banco descartável cujo dono NÃO seja superusuário.
"""
import os
import unittest
import uuid

URL = os.environ.get("NEXORA_TEST_DATABASE_URL")
if URL:
    os.environ.update({"CENTRAL_DATABASE_URL": URL, "REDIS_URL": "redis://localhost:6399/0", "CENTRAL_SECRET_KEY": "x" * 40,
                       "ZABBIX_API_URL": "http://z", "ZABBIX_API_TOKEN": "t"})
    from central_monitor import db, worker
    from central_monitor.db import connect, tenant_connection


@unittest.skipUnless(URL, "NEXORA_TEST_DATABASE_URL not set")
class FailureGraceFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.migrate()
        with connect() as c:
            ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
            cls.tenant = str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,%s,%s) RETURNING id",
                                       (ws, "Empresa Carencia", "carencia-" + uuid.uuid4().hex[:6])).fetchone()["id"])
        with tenant_connection(cls.tenant) as c:
            cls.asset = c.execute("INSERT INTO cm_assets(tenant_id,name,kind) VALUES (%s,'srv','database') RETURNING id", (cls.tenant,)).fetchone()["id"]

    def monitor(self, monitor_type="push", grace=None):
        with tenant_connection(self.tenant) as c:
            return dict(c.execute(
                """INSERT INTO cm_monitors(tenant_id,asset_id,name,monitor_type,configuration,severity,failure_grace_seconds)
                VALUES (%s,%s,%s,%s,'{}'::jsonb,'high',%s) RETURNING *""",
                (self.tenant, self.asset, "m-" + uuid.uuid4().hex[:8], monitor_type, grace)).fetchone())

    def counts(self, monitor):
        with tenant_connection(self.tenant) as c:
            alerts = c.execute("SELECT count(*) AS n FROM cm_alerts WHERE monitor_id=%s", (monitor["id"],)).fetchone()["n"]
            incidents = c.execute("SELECT count(*) AS n FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id WHERE a.monitor_id=%s", (monitor["id"],)).fetchone()["n"]
            state = c.execute("SELECT last_status,failing_since FROM cm_monitors WHERE id=%s", (monitor["id"],)).fetchone()
        return alerts, incidents, state

    def age_failure(self, monitor, minutes):
        with tenant_connection(self.tenant) as c:
            c.execute("UPDATE cm_monitors SET failing_since=now()-make_interval(mins => %s) WHERE id=%s", (minutes, monitor["id"]))

    def test_short_outage_never_becomes_an_incident(self):
        m = self.monitor()
        worker.record_result(m, False, "pipes nomeados")
        alerts, incidents, state = self.counts(m)
        self.assertEqual((alerts, incidents), (0, 0))
        self.assertEqual(state["last_status"], "down")  # o painel continua mostrando a falha
        self.assertIsNotNone(state["failing_since"])
        worker.record_result(m, True, "ok")
        alerts, incidents, state = self.counts(m)
        self.assertEqual((alerts, incidents), (0, 0))
        self.assertIsNone(state["failing_since"])  # recuperou: a contagem recomeça do zero

    def test_failure_that_lasts_opens_exactly_one_incident_and_recovery_closes_it(self):
        m = self.monitor()
        worker.record_result(m, False, "falha")
        self.age_failure(m, 11)  # passou da carência de 10 min
        worker.record_result(m, False, "falha")
        worker.record_result(m, False, "falha de novo")
        alerts, incidents, _ = self.counts(m)
        self.assertEqual((alerts, incidents), (1, 1))
        worker.record_result(m, True, "ok")
        with tenant_connection(self.tenant) as c:
            row = c.execute("""SELECT i.status AS incident, a.status AS alert FROM cm_incidents i JOIN cm_alerts a ON a.id=i.alert_id
                WHERE a.monitor_id=%s""", (m["id"],)).fetchone()
        self.assertEqual((row["incident"], row["alert"]), ("resolved", "resolved"))

    def test_http_monitor_still_opens_immediately(self):
        m = self.monitor("http")
        worker.record_result(m, False, "timeout")
        alerts, incidents, _ = self.counts(m)
        self.assertEqual((alerts, incidents), (1, 1))

    def test_explicit_zero_grace_on_agent_monitor_opens_immediately(self):
        m = self.monitor("push", grace=0)
        worker.record_result(m, False, "falha")
        self.assertEqual(self.counts(m)[:2], (1, 1))

    def test_custom_grace_is_respected(self):
        m = self.monitor("push", grace=1800)
        worker.record_result(m, False, "falha")
        self.age_failure(m, 20)  # 20 min < 30 min
        worker.record_result(m, False, "falha")
        self.assertEqual(self.counts(m)[:2], (0, 0))
        self.age_failure(m, 31)
        worker.record_result(m, False, "falha")
        self.assertEqual(self.counts(m)[:2], (1, 1))


if __name__ == "__main__":
    unittest.main()
