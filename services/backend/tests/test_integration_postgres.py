"""End-to-end checks against a real PostgreSQL (migrations, forced RLS, state machines).

Opt-in: set NEXORA_TEST_DATABASE_URL to a database owned by a NON-superuser role (superusers bypass RLS), e.g.
  postgresql://central_app:pw@localhost:55432/central_monitor
The database is migrated and filled with throw-away rows: use a disposable database, never a real one.
"""
import base64
import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest import mock

URL = os.environ.get("NEXORA_TEST_DATABASE_URL")
if URL:
    os.environ.update({
        "CENTRAL_DATABASE_URL": URL, "REDIS_URL": "redis://localhost:6399/0", "CENTRAL_SECRET_KEY": "x" * 40, "ZABBIX_API_URL": "http://z", "ZABBIX_API_TOKEN": "t",
        "PUSH_TOKEN_KEY": __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
        "EXPO_PROJECT_IDS": "11111111-2222-3333-4444-555555555555",
        "BUSINESS_SIGNING_KEY": base64.b64encode(os.urandom(32)).decode(),
    })
    from fastapi import HTTPException
    from central_monitor import business, business_monitors as bm, business_results as br, db, expo_push as ep
    from central_monitor.auth import Identity
    from central_monitor.db import connect, tenant_connection
    from central_monitor.main import ensure_user


def ident(name):
    return Identity(subject=f"{name}-{uuid.uuid4().hex[:6]}", email=f"{name}-{uuid.uuid4().hex[:6]}@t.test", display_name=name)


@unittest.skipUnless(URL, "NEXORA_TEST_DATABASE_URL not set")
class PostgresFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.migrate()
        with connect() as c:
            ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
            cls.tenants = []
            for name in ("Empresa A", "Empresa B"):
                cls.tenants.append(str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,%s,%s) RETURNING id",
                                                 (ws, name, name.lower().replace(" ", "-") + uuid.uuid4().hex[:5])).fetchone()["id"]))
        cls.A, cls.B = cls.tenants
        cls.master_ident = ident("master")
        first = ensure_user(cls.master_ident)
        if not first["is_master"]:  # database already had a master: make this identity one for the test
            with connect() as c:
                c.execute("UPDATE cm_users SET is_master=true WHERE id=%s", (first["id"],))
        cls.master = first
        cls.user_ident = ident("operador")
        cls.user = ensure_user(cls.user_ident)
        for tenant in cls.tenants:
            with tenant_connection(tenant) as c:
                ws = c.execute("SELECT workspace_id FROM cm_tenants WHERE id=%s", (tenant,)).fetchone()["workspace_id"]
                c.execute("""INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role,row_scope,granted_by,reason)
                    VALUES (%s,%s,%s,'admin','company',%s,'test')""", (ws, tenant, cls.user["id"], cls.master["id"]))
        business.enable_business(business.EnableChange(enabled=True, reason="integration test"), cls.A, cls.master_ident)

    # ------------------------------------------------------------------ helpers
    def indicator(self):
        with tenant_connection(self.A) as c:
            row = c.execute("""INSERT INTO cm_business_indicators(tenant_id,code,name,unit,direction,warning_value,critical_value,
                  stale_after_seconds,owner_user_id) VALUES (%s,%s,'Contas vencidas','reais','above',100,1000,3600,%s) RETURNING id""",
                            (self.A, "iv_" + uuid.uuid4().hex[:8], self.master["id"])).fetchone()
            c.execute("INSERT INTO cm_business_states(tenant_id,indicator_id) VALUES (%s,%s)", (self.A, row["id"]))
        return row["id"]

    def setup_monitor(self):
        worker = bm.create_worker(bm.WorkerCreate(name="w-" + uuid.uuid4().hex[:6]), self.A, self.master_ident)
        source = bm.create_source(bm.SourceCreate(name="s-" + uuid.uuid4().hex[:6], worker_id=worker["id"], dialect="sqlserver",
                                                  database_ref="erp_principal"), self.A, self.master_ident)
        monitor = bm.create_monitor(bm.MonitorCreate(
            name="m-" + uuid.uuid4().hex[:6], indicator_id=self.indicator(), source_id=source["id"], interval_seconds=60,
            query="SELECT documento, valor_vencido FROM dbo.vw_nexora_cr WHERE vencimento < @hoje", parameters={"hoje": "$today"},
            columns=[{"name": "documento", "type": "string"}, {"name": "valor_vencido", "type": "decimal"}],
            metric={"kind": "sum", "column": "valor_vencido"}), self.A, self.master_ident)
        auth = bm.worker_identity(self.A, str(worker["id"]), worker["worker_token"])
        return worker, monitor, auth

    def run_job(self, auth, rows, execution=None):
        jobs = bm.claim_jobs(limit=1, worker=auth)["jobs"]
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertTrue(br.verify_manifest({k: v for k, v in job.items() if k != "signature"}, job["signature"], br.public_key_b64()))
        bm.job_started(uuid.UUID(job["execution_id"]), auth)
        result = bm.ResultBody(started_at=datetime.now(UTC), duration_ms=40, columns=job["column_schema"], rows=rows,
                               row_count=len(rows), truncated=False)
        return job, bm.job_result(uuid.UUID(job["execution_id"]), result, auth), result

    def publish(self, monitor, auth):
        bm.test_revision(monitor["id"], 1, self.A, self.master_ident)
        job, outcome, _ = self.run_job(auth, [{"documento": "T", "valor_vencido": "1"}])  # the test execution
        self.assertTrue(job["test"] and outcome["test"])
        return bm.publish_revision(monitor["id"], 1, self.A, self.master_ident)

    # ------------------------------------------------------------------ governance (H02/H03)
    def test_only_master_governs_sql_and_bad_sql_is_rejected(self):
        for call in (lambda: bm.create_worker(bm.WorkerCreate(name="x1"), self.A, self.user_ident),
                     lambda: bm.list_sources(self.A, self.user_ident)):
            with self.assertRaises(HTTPException) as ctx:
                call()
            self.assertEqual(ctx.exception.status_code, 403)
        worker, monitor, auth = self.setup_monitor()
        with self.assertRaises(HTTPException) as ctx:
            bm.new_revision(monitor["id"], bm.RevisionBody(query="DELETE FROM x", columns=[{"name": "a", "type": "string"}],
                                                          metric={"kind": "row_count"}), self.A, self.master_ident)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_publish_requires_a_successful_test(self):
        worker, monitor, auth = self.setup_monitor()
        with self.assertRaises(HTTPException) as ctx:
            bm.publish_revision(monitor["id"], 1, self.A, self.master_ident)
        self.assertEqual(ctx.exception.status_code, 409)

    # ------------------------------------------------------------------ full cycle (H04/H07/H12)
    def test_full_cycle_feeds_indicator_once_and_is_idempotent(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        self.assertEqual(bm.run_scheduler(connect)["scheduled"] >= 1, True)
        rows = [{"documento": "CR-1", "valor_vencido": "700.50"}, {"documento": "CR-2", "valor_vencido": None}]
        job, outcome, result = self.run_job(auth, rows)
        self.assertEqual(outcome["indicator_updated"], True)
        with tenant_connection(self.A) as c:
            state = c.execute("SELECT s.state,s.last_value FROM cm_business_states s JOIN cm_business_monitors m ON m.indicator_id=s.indicator_id "
                              "WHERE m.id=%s", (monitor["id"],)).fetchone()
        self.assertEqual(str(state["last_value"]), "700.500000")
        self.assertEqual(state["state"], "atencao")  # 700.50 >= warning 100, < critical 1000
        again = bm.job_result(uuid.UUID(job["execution_id"]), result, auth)  # H07: same answer twice
        self.assertEqual(again["status"], "duplicate")
        with tenant_connection(self.A) as c:
            n = c.execute("SELECT count(*) AS n FROM cm_business_values WHERE execution_id=%s", (job["execution_id"],)).fetchone()["n"]
            occurrences = c.execute("SELECT count(*) AS n FROM cm_business_occurrences o JOIN cm_business_monitors m ON m.indicator_id=o.indicator_id "
                                    "WHERE m.id=%s", (monitor["id"],)).fetchone()["n"]
        self.assertEqual((n, occurrences), (1, 1))
        different = bm.ResultBody(started_at=datetime.now(UTC), duration_ms=1, columns=job["column_schema"],
                                  rows=[{"documento": "X", "valor_vencido": "1"}], row_count=1)
        with self.assertRaises(HTTPException) as ctx:
            bm.job_result(uuid.UUID(job["execution_id"]), different, auth)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_empty_result_is_a_success_with_zero_value(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job, outcome, _ = self.run_job(auth, [])
        self.assertEqual(outcome["status"], "accepted")
        with tenant_connection(self.A) as c:
            row = c.execute("SELECT state,row_count FROM cm_business_executions WHERE id=%s", (job["execution_id"],)).fetchone()
        self.assertEqual((row["state"], row["row_count"]), ("SUCCEEDED", 0))

    def test_contract_violation_is_rejected_and_counts_as_failure(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = bm.claim_jobs(limit=1, worker=auth)["jobs"][0]
        bad = bm.ResultBody(started_at=datetime.now(UTC), duration_ms=5, columns=job["column_schema"],
                            rows=[{"documento": "A"}], row_count=1)  # missing a column
        self.assertEqual(bm.job_result(uuid.UUID(job["execution_id"]), bad, auth)["reason"], "contract_violation")

    # ------------------------------------------------------------------ isolation (H09) and RLS
    def test_other_tenant_cannot_see_or_touch_executions(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = bm.claim_jobs(limit=1, worker=auth)["jobs"][0]
        with tenant_connection(self.B) as c:
            self.assertEqual(c.execute("SELECT count(*) AS n FROM cm_business_executions").fetchone()["n"], 0)
            self.assertEqual(c.execute("SELECT count(*) AS n FROM cm_business_monitors").fetchone()["n"], 0)
        with self.assertRaises(HTTPException) as ctx:  # a worker from B asking for A's execution id
            bm.worker_identity(self.B, str(worker["id"]), worker["worker_token"])
        self.assertEqual(ctx.exception.status_code, 401)
        with self.assertRaises(HTTPException) as ctx:  # right tenant header, wrong token
            bm.worker_identity(self.A, str(worker["id"]), "wrong")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertTrue(job["execution_id"])

    # ------------------------------------------------------------------ failures, breaker, supersede, revoke
    def test_retry_then_circuit_breaker_suspends_only_the_monitor(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        for _ in range(bm.SUSPEND_AFTER_FAILURES):
            bm.run_scheduler(connect)
            with tenant_connection(self.A) as c:  # make the monitor due again
                c.execute("UPDATE cm_business_monitors SET last_scheduled_at=now()-interval '1 day' WHERE id=%s AND status='ACTIVE'", (monitor["id"],))
            bm.run_scheduler(connect)
            jobs = bm.claim_jobs(limit=1, worker=auth)["jobs"]
            if not jobs:
                break
            bm.job_failed(uuid.UUID(jobs[0]["execution_id"]),
                          bm.FailureBody(status="FAILED", error_code="sql_error", retryable=False), auth)
        with tenant_connection(self.A) as c:
            row = c.execute("SELECT status,suspended_reason FROM cm_business_monitors WHERE id=%s", (monitor["id"],)).fetchone()
        self.assertEqual((row["status"], row["suspended_reason"]), ("SUSPENDED", "circuit_breaker"))

    def test_transient_failure_returns_job_to_queue_with_backoff(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = bm.claim_jobs(limit=1, worker=auth)["jobs"][0]
        out = bm.job_failed(uuid.UUID(job["execution_id"]), bm.FailureBody(status="TIMED_OUT", error_code="timeout", retryable=True), auth)
        self.assertEqual(out["state"], "PENDING")
        self.assertEqual(bm.claim_jobs(limit=1, worker=auth)["jobs"], [])  # backoff: not claimable immediately

    def test_revoked_worker_is_cut_off_and_queued_jobs_canceled(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        bm.revoke_worker(worker["id"], self.A, self.master_ident)
        with self.assertRaises(HTTPException) as ctx:
            bm.worker_identity(self.A, str(worker["id"]), worker["worker_token"])
        self.assertEqual(ctx.exception.status_code, 401)
        with tenant_connection(self.A) as c:
            n = c.execute("SELECT count(*) AS n FROM cm_business_executions WHERE worker_id=%s AND state='PENDING'", (worker["id"],)).fetchone()["n"]
        self.assertEqual(n, 0)

    def test_disabling_a_monitor_cancels_queued_work(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        bm.change_status(monitor["id"], bm.StatusChange(action="disable", reason="manutencao"), self.A, self.master_ident)
        self.assertEqual(bm.claim_jobs(limit=1, worker=auth)["jobs"], [])

    # ------------------------------------------------------------------ push (PUSH-005/006)
    def test_push_pipeline_send_receipt_and_access_checks(self):
        token = lambda s: f"ExponentPushToken[{s}abcdefgh]"
        with connect() as c:
            c.execute("""INSERT INTO cm_push_devices(user_id,installation_id,platform,token_cipher,token_hash,token_hint,expo_project_id)
                VALUES (%s,%s,'android',%s,%s,%s,'p')""", (self.user["id"], "install-" + uuid.uuid4().hex[:8], ep.encrypt_token(token("u1")),
                                                          ep.token_hash(token("u1") + uuid.uuid4().hex), ep.token_hint(token("u1"))))
        with tenant_connection(self.A) as c:
            nid = ep.enqueue_notification(c, self.A, domain="INFRA", source_type="incident", source_id="i-" + uuid.uuid4().hex[:6],
                                          event_key="ev-" + uuid.uuid4().hex, severity="critical", category="incident", title="T", body="B",
                                          action_type="incident", action_ref="i1", recipients=[self.user["id"]], requires_ack=True)
            business_nid = ep.enqueue_notification(c, self.A, domain="BUSINESS", source_type="o", source_id="o1", event_key="b-" + uuid.uuid4().hex,
                                                   severity="high", category="x", title="T", body="B", action_type="business_occurrence",
                                                   action_ref="o1", recipients=[self.user["id"]])
        self.assertIsNotNone(nid)
        with tenant_connection(self.A) as c:  # infra access does not imply business push
            self.assertEqual(c.execute("SELECT count(*) AS n FROM cm_notification_recipients WHERE notification_id=%s", (business_nid,)).fetchone()["n"], 0)

        def fake_post(path, body):
            if path == "send":
                return {"data": [{"status": "ok", "id": "T-" + uuid.uuid4().hex[:6]} for _ in body]}
            return {"data": {i: {"status": "ok"} for i in body["ids"]}}

        with mock.patch.object(ep, "_expo_post", side_effect=fake_post):
            self.assertGreaterEqual(ep.send_pending(self.A), 1)
            with tenant_connection(self.A) as c:
                c.execute("UPDATE cm_push_expo_deliveries SET sent_at=now()-interval '20 minutes' WHERE state='SENT_TO_EXPO'")
            self.assertGreaterEqual(ep.check_receipts(self.A), 1)
        with tenant_connection(self.A) as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM cm_push_expo_deliveries WHERE state='PROVIDER_ACCEPTED'").fetchone())
        opened = ep.open_notification(nid, self.A, self.user_ident)
        self.assertEqual(opened["action_type"], "incident")
        self.assertTrue(ep.acknowledge_notification(nid, self.A, self.user_ident)["acknowledged"])
        with tenant_connection(self.A) as c:  # access revoked afterwards: same neutral 404
            c.execute("UPDATE cm_access_grants SET active=false WHERE user_id=%s AND tenant_id=%s", (self.user["id"], self.A))
        with self.assertRaises(HTTPException):
            ep.open_notification(nid, self.A, self.user_ident)


if __name__ == "__main__":
    unittest.main()
