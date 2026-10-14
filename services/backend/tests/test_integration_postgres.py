"""End-to-end checks against a real PostgreSQL (migrations, forced RLS, state machines).

Opt-in: set NEXORA_TEST_DATABASE_URL to a database owned by a NON-superuser role (superusers bypass RLS), e.g.
  postgresql://central_app:pw@localhost:55432/central_monitor
The database is migrated and filled with throw-away rows: use a disposable database, never a real one.
"""
import base64
import json
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
    from central_monitor import business_broker, business_governance as bg, worker as cm_worker
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
        with connect() as c:
            existing = c.execute("SELECT * FROM cm_users WHERE is_master").fetchone()
        if existing:  # a previous class already created the (single) technical Master: reuse that identity
            cls.master_ident = Identity(subject=existing["external_subject"], email=existing["email"], display_name=existing["display_name"])
            cls.master = ensure_user(cls.master_ident)
        else:
            cls.master_ident = ident("master")
            cls.master = ensure_user(cls.master_ident)  # the first user of an empty database is the Master
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

    def pump(self):
        """Run the transactional outbox (a no-op in db mode, publishes to the broker otherwise)."""
        cm_worker.process_outbox()

    def claim_one(self, auth):
        self.pump()
        jobs = bm.claim_jobs(limit=1, worker=auth)["jobs"]
        if not jobs:
            return None
        envelope = jobs[0]
        self.assertEqual(set(envelope), {"payload", "signature"})
        # The worker verifies the signature over exactly these bytes, then parses the job from them.
        self.assertTrue(br.verify_manifest(json.loads(envelope["payload"]), envelope["signature"], br.public_key_b64()))
        return json.loads(envelope["payload"])

    def run_job(self, auth, rows, execution=None):
        job = self.claim_one(auth)
        self.assertIsNotNone(job)
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


    # ------------------------------------------------------------------ Master governance views (metadata only)
    def test_master_views_expose_metadata_without_business_rows(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        workers = bg.list_workers_for_master(self.A, self.master_ident)
        self.assertTrue(any(str(w["id"]) == str(worker["id"]) and w["sources"] == 1 for w in workers))
        monitors = bg.list_monitors_for_master(self.A, self.master_ident)
        mine = next(m for m in monitors if str(m["id"]) == str(monitor["id"]))
        self.assertEqual((mine["status"], mine["published_revision"], mine["dialect"]), ("ACTIVE", 1, "sqlserver"))
        catalogue = bg.list_indicators_for_master(self.A, self.master_ident)
        self.assertTrue(any(i["has_monitor"] for i in catalogue) and all("last_value" not in i for i in catalogue))
        detail = bg.monitor_revisions(monitor["id"], self.A, self.master_ident)
        revision = detail["revisions"][0]
        self.assertEqual((revision["state"], revision["test_state"]), ("PUBLISHED", "SUCCEEDED"))
        self.assertIn("@hoje", revision["query_text"])
        meta = bg.execution_metadata(uuid.UUID(str(revision["test_execution_id"])), self.A, self.master_ident)
        self.assertNotIn("rows", meta)
        self.assertEqual([c["name"] for c in meta["columns"]], ["documento", "valor_vencido"])  # names and types only
        for call in (lambda: bg.list_workers_for_master(self.A, self.user_ident), lambda: bg.list_monitors_for_master(self.A, self.user_ident),
                     lambda: bg.monitor_revisions(monitor["id"], self.A, self.user_ident),
                     lambda: bg.execution_metadata(uuid.UUID(str(revision["test_execution_id"])), self.A, self.user_ident)):
            with self.assertRaises(HTTPException) as ctx:
                call()
            self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(HTTPException) as ctx:  # another company's id is the same neutral 404
            bg.execution_metadata(uuid.UUID(str(revision["test_execution_id"])), self.B, self.master_ident)
        self.assertIn(ctx.exception.status_code, (403, 404))

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
        job = self.claim_one(auth)
        bad = bm.ResultBody(started_at=datetime.now(UTC), duration_ms=5, columns=job["column_schema"],
                            rows=[{"documento": "A"}], row_count=1)  # missing a column
        self.assertEqual(bm.job_result(uuid.UUID(job["execution_id"]), bad, auth)["reason"], "contract_violation")

    # ------------------------------------------------------------------ isolation (H09) and RLS
    def test_other_tenant_cannot_see_or_touch_executions(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = self.claim_one(auth)
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
            job = self.claim_one(auth)
            if not job:
                break
            bm.job_failed(uuid.UUID(job["execution_id"]),
                          bm.FailureBody(status="FAILED", error_code="sql_error", retryable=False), auth)
        with tenant_connection(self.A) as c:
            row = c.execute("SELECT status,suspended_reason FROM cm_business_monitors WHERE id=%s", (monitor["id"],)).fetchone()
        self.assertEqual((row["status"], row["suspended_reason"]), ("SUSPENDED", "circuit_breaker"))

    def test_transient_failure_returns_job_to_queue_with_backoff(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = self.claim_one(auth)
        out = bm.job_failed(uuid.UUID(job["execution_id"]), bm.FailureBody(status="TIMED_OUT", error_code="timeout", retryable=True), auth)
        self.assertEqual(out["state"], "PENDING")
        self.assertIsNone(self.claim_one(auth))  # backoff: not claimable immediately

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
        self.assertIsNone(self.claim_one(auth))

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


@unittest.skipUnless(URL, "NEXORA_TEST_DATABASE_URL not set")
class BrokerMode(PostgresFlows):
    """The same flows with the broker in the middle (in-memory double with the RabbitMQ contract), plus its failure modes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        self.broker = business_broker.InMemoryJobBroker()
        business_broker.set_broker_for_tests(self.broker)
        self.addCleanup(business_broker.set_broker_for_tests, None)

    def exec_state(self, execution_id):
        with tenant_connection(self.A) as c:
            return c.execute("SELECT state,attempts,published_at FROM cm_business_executions WHERE id=%s", (execution_id,)).fetchone()

    def test_schedule_publish_claim_ack(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)  # test execution also travelled through the broker
        bm.run_scheduler(connect)
        self.pump()
        self.assertEqual(self.broker.stats([worker["id"]])["queues"][str(worker["id"])], 1)
        job = self.claim_one(auth)
        self.assertEqual(self.broker.stats([worker["id"]])["queues"][str(worker["id"])], 0)  # acked after the lease committed
        self.assertEqual(self.exec_state(job["execution_id"])["state"], "LEASED")
        self.assertIsNotNone(self.exec_state(job["execution_id"])["published_at"])

    def test_duplicate_message_does_not_create_a_second_lease(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        self.pump()
        message = dict(self.broker.queues[str(worker["id"])][0])
        self.broker.publish(message, worker["id"], 60000)  # the broker redelivered / published twice
        job = self.claim_one(auth)
        self.assertIsNotNone(job)
        bm.job_started(uuid.UUID(job["execution_id"]), auth)
        self.assertIsNone(self.claim_one(auth))  # second copy: stale, acked and dropped (worker is busy anyway)
        self.assertEqual(self.exec_state(job["execution_id"])["attempts"], 1)

    def test_poison_and_foreign_messages_go_to_the_dead_letter_queue(self):
        worker, monitor, auth = self.setup_monitor()
        self.broker.publish({"execution_id": str(uuid.uuid4()), "tenant_id": self.A, "worker_id": str(worker["id"])}, worker["id"], 60000)
        self.broker.publish({"execution_id": str(uuid.uuid4()), "tenant_id": self.B, "worker_id": str(worker["id"])}, worker["id"], 60000)
        self.assertIsNone(self.claim_one(auth))
        self.assertIsNone(self.claim_one(auth))
        self.assertEqual(self.broker.stats([worker["id"]])["dlq_depth"], 2)
        listed = bm.dead_letters(self.A, self.master_ident, 50)
        self.assertEqual(len(listed), 1)  # only this company's dead letters are visible

    def test_broker_outage_keeps_the_outbox_and_recovers(self):
        worker, monitor, auth = self.setup_monitor()
        bm.test_revision(monitor["id"], 1, self.A, self.master_ident)
        self.broker.fail_publish = True
        self.pump()
        self.assertIsNone(self.broker.queues.get(str(worker["id"])) or None)  # nothing published
        with tenant_connection(self.A) as c:
            row = c.execute("SELECT state,attempts FROM cm_outbox WHERE topic='business.job.publish' AND tenant_id=%s ORDER BY id DESC LIMIT 1", (self.A,)).fetchone()
            c.execute("UPDATE cm_outbox SET available_at=now() WHERE topic='business.job.publish' AND tenant_id=%s AND state='retry_wait'", (self.A,))
        self.assertEqual((row["state"], row["attempts"]), ("retry_wait", 1))
        self.broker.fail_publish = False
        self.assertIsNotNone(self.claim_one(auth))  # pump inside claim_one republished it

    def test_claim_reports_503_when_the_broker_is_down(self):
        worker, monitor, auth = self.setup_monitor()
        with mock.patch.object(self.broker, "consume", side_effect=business_broker.BrokerUnavailable("down")):
            with self.assertRaises(HTTPException) as ctx:
                bm.claim_jobs(limit=1, worker=auth)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_lost_lease_is_requeued_and_republished(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = self.claim_one(auth)
        with tenant_connection(self.A) as c:  # the worker died: lease expired, window still open
            c.execute("UPDATE cm_business_executions SET lease_expires_at=now()-interval '1 second',state='RUNNING' WHERE id=%s", (job["execution_id"],))
        bm.run_scheduler(connect)
        self.assertEqual(self.exec_state(job["execution_id"])["state"], "PENDING")
        with tenant_connection(self.A) as c:
            c.execute("UPDATE cm_business_executions SET available_at=now() WHERE id=%s", (job["execution_id"],))
            c.execute("UPDATE cm_outbox SET available_at=now() WHERE topic='business.job.publish' AND tenant_id=%s AND state IN ('pending','retry_wait')", (self.A,))
        again = self.claim_one(auth)
        self.assertEqual(again["execution_id"], job["execution_id"])
        self.assertEqual(self.exec_state(job["execution_id"])["attempts"], 2)

    def test_broker_status_is_visible_to_master_only(self):
        worker, monitor, auth = self.setup_monitor()
        self.assertEqual(bm.broker_status(self.A, self.master_ident)["mode"], "memory")
        with self.assertRaises(HTTPException):
            bm.broker_status(self.A, self.user_ident)


    # ------------------------------------------------------------------ dead-letter reprocess / discard
    def dead_letter(self, execution_id, state="EXPIRED"):
        with tenant_connection(self.A) as c:
            c.execute("UPDATE cm_business_executions SET state=%s,finished_at=now(),error_code='missed_window' WHERE id=%s", (state, execution_id))
        self.broker.dlq.append({"execution_id": str(execution_id), "tenant_id": self.A})

    def test_reprocess_creates_a_new_execution_and_clears_the_dead_letter(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = self.claim_one(auth)
        old = uuid.UUID(job["execution_id"])
        self.dead_letter(old, "FAILED")
        body = bg.DeadLetterAction(reason="reenvio apos queda do broker")
        outcome = bg.reprocess_dead_letter(old, body, self.A, self.master_ident)
        self.assertEqual(outcome["dead_letters_removed"], 1)
        self.assertEqual(self.broker.stats([worker["id"]])["dlq_depth"], 0)
        again = self.claim_one(auth)
        self.assertEqual(again["execution_id"], str(outcome["execution_id"]))
        self.assertNotEqual(again["execution_id"], job["execution_id"])
        self.assertEqual(self.exec_state(old)["state"], "FAILED")  # the old one stays as evidence
        with self.assertRaises(HTTPException) as ctx:  # one in flight now
            bg.reprocess_dead_letter(old, body, self.A, self.master_ident)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_reprocess_refuses_when_the_monitor_would_not_run_it(self):
        worker, monitor, auth = self.setup_monitor()
        self.publish(monitor, auth)
        bm.run_scheduler(connect)
        job = self.claim_one(auth)
        eid = uuid.UUID(job["execution_id"])
        self.dead_letter(eid)
        bm.change_status(monitor["id"], bm.StatusChange(action="suspend", reason="manutencao"), self.A, self.master_ident)
        with self.assertRaises(HTTPException) as ctx:
            bg.reprocess_dead_letter(eid, bg.DeadLetterAction(reason="tentativa"), self.A, self.master_ident)
        self.assertEqual(ctx.exception.status_code, 409)
        with self.assertRaises(HTTPException) as ctx:  # finished executions are not reprocessable
            with tenant_connection(self.A) as c:
                c.execute("UPDATE cm_business_executions SET state='SUCCEEDED' WHERE id=%s", (eid,))
            bg.reprocess_dead_letter(eid, bg.DeadLetterAction(reason="tentativa"), self.A, self.master_ident)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_discard_removes_only_that_execution_and_requires_master(self):
        worker, monitor, auth = self.setup_monitor()
        keep, drop = uuid.uuid4(), uuid.uuid4()
        self.broker.dlq.extend([{"execution_id": str(keep), "tenant_id": self.A}, {"execution_id": str(drop), "tenant_id": self.A},
                                {"execution_id": str(drop), "tenant_id": self.B}])
        with self.assertRaises(HTTPException) as ctx:
            bg.discard_dead_letter(drop, bg.DeadLetterAction(reason="descarte"), self.A, self.user_ident)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(bg.discard_dead_letter(drop, bg.DeadLetterAction(reason="descarte"), self.A, self.master_ident)["dead_letters_removed"], 1)
        self.assertEqual({(b["execution_id"], b["tenant_id"]) for b in self.broker.dlq}, {(str(keep), self.A), (str(drop), self.B)})


if __name__ == "__main__":
    unittest.main()
