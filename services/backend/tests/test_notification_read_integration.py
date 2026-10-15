"""APP-01: leitura individual e em lote das notificações (PostgreSQL real, opt-in)."""
import os
import unittest
import uuid

URL = os.environ.get("NEXORA_TEST_DATABASE_URL")
if URL:
    os.environ.update({"CENTRAL_DATABASE_URL": URL, "REDIS_URL": "redis://localhost:6399/0", "CENTRAL_SECRET_KEY": "x" * 40,
                       "ZABBIX_API_URL": "http://z", "ZABBIX_API_TOKEN": "t", "EXPO_PROJECT_IDS": "proj-test-0001"})
    from fastapi import HTTPException
    from central_monitor import db, expo_push as ep
    from central_monitor.auth import Identity
    from central_monitor.db import connect, tenant_connection
    from central_monitor.main import ensure_user


def identity(name):
    return Identity(subject=f"{name}-{uuid.uuid4().hex[:6]}", email=f"{name}-{uuid.uuid4().hex[:6]}@t.test", display_name=name)


@unittest.skipUnless(URL, "NEXORA_TEST_DATABASE_URL not set")
class ReadNotifications(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.migrate()
        with connect() as c:
            ws = c.execute("SELECT id FROM cm_workspaces WHERE code='default'").fetchone()["id"]
            cls.A, cls.B = [str(c.execute("INSERT INTO cm_tenants(workspace_id,name,slug) VALUES (%s,%s,%s) RETURNING id",
                                          (ws, n, n.lower() + uuid.uuid4().hex[:5])).fetchone()["id"]) for n in ("Empresa A", "Empresa B")]
        cls.ws = ws
        cls.me, cls.other = identity("eu"), identity("outro")
        cls.me_user, cls.other_user = ensure_user(cls.me), ensure_user(cls.other)
        for tenant in (cls.A, cls.B):
            for user in (cls.me_user, cls.other_user):
                with tenant_connection(tenant) as c:
                    c.execute("INSERT INTO cm_access_grants(workspace_id,tenant_id,user_id,role) VALUES (%s,%s,%s,'operator')", (ws, tenant, user["id"]))

    def notify(self, tenant, recipients, key=None):
        with tenant_connection(tenant) as c:
            return ep.enqueue_notification(c, tenant, domain="INFRA", source_type="incident", source_id=str(uuid.uuid4()),
                                           event_key=key or f"t:{uuid.uuid4()}", severity="P1", category="indisponibilidade", title="t", body="b",
                                           action_type="INCIDENT", action_ref=str(uuid.uuid4()), recipients=recipients)

    def unread(self, tenant, identity_):
        return ep.list_notifications(only_unread=True, severity=None, limit=200, offset=0, tenant_id=tenant, identity=identity_)["totals"]["unread"]

    def test_mark_one_read_removes_it_from_pending_and_is_idempotent(self):
        before = self.unread(self.A, self.me)
        nid = self.notify(self.A, [self.me_user["id"]])
        self.assertEqual(self.unread(self.A, self.me), before + 1)
        self.assertEqual(ep.mark_notification_read(nid, tenant_id=self.A, identity=self.me), {"id": str(nid), "read": True})
        self.assertEqual(self.unread(self.A, self.me), before)
        with tenant_connection(self.A) as c:
            first = c.execute("SELECT opened_at FROM nx_notification_recipients WHERE notification_id=%s AND user_id=%s", (nid, self.me_user["id"])).fetchone()["opened_at"]
        ep.mark_notification_read(nid, tenant_id=self.A, identity=self.me)  # repetido: sem erro e sem mudar a data
        with tenant_connection(self.A) as c:
            again = c.execute("SELECT opened_at FROM nx_notification_recipients WHERE notification_id=%s AND user_id=%s", (nid, self.me_user["id"])).fetchone()["opened_at"]
        self.assertEqual(first, again)

    def test_mark_all_clears_only_my_pending_in_this_company(self):
        for _ in range(3):
            self.notify(self.A, [self.me_user["id"], self.other_user["id"]])
        self.notify(self.B, [self.me_user["id"]])
        other_before = self.unread(self.A, self.other)
        b_before = self.unread(self.B, self.me)
        result = ep.mark_all_read(tenant_id=self.A, identity=self.me)
        self.assertGreaterEqual(result["updated"], 3)
        self.assertEqual(self.unread(self.A, self.me), 0)
        self.assertEqual(self.unread(self.A, self.other), other_before)  # outro usuário não é afetado
        self.assertEqual(self.unread(self.B, self.me), b_before)         # outra empresa não é afetada
        self.assertEqual(ep.mark_all_read(tenant_id=self.A, identity=self.me), {"updated": 0})  # idempotente

    def test_read_items_are_not_deleted_and_the_action_is_audited(self):
        nid = self.notify(self.A, [self.me_user["id"]])
        ep.mark_all_read(tenant_id=self.A, identity=self.me)
        with tenant_connection(self.A) as c:
            self.assertIsNotNone(c.execute("SELECT 1 FROM nx_notifications WHERE id=%s", (nid,)).fetchone())  # evento preservado
            audit = c.execute("SELECT count(*) AS n FROM nx_push_audit WHERE action='notification.read_all' AND actor=%s", (self.me_user["id"],)).fetchone()["n"]
        self.assertGreaterEqual(audit, 1)
        listed = ep.list_notifications(only_unread=False, severity=None, limit=200, offset=0, tenant_id=self.A, identity=self.me)
        self.assertIn(str(nid), [str(i["id"]) for i in listed["items"]])  # aparece em "Todas", fora de "Não lidas"

    def test_someone_without_access_cannot_read_or_mark_in_that_company(self):
        stranger = identity("estranho")
        ensure_user(stranger)
        nid = self.notify(self.A, [self.me_user["id"]])
        with self.assertRaises(HTTPException):
            ep.mark_all_read(tenant_id=self.A, identity=stranger)
        with self.assertRaises(HTTPException):
            ep.mark_notification_read(nid, tenant_id=self.A, identity=stranger)

    def test_cannot_mark_someone_elses_notification(self):
        nid = self.notify(self.A, [self.other_user["id"]])  # destinatário é o "outro"
        with self.assertRaises(HTTPException) as error:
            ep.mark_notification_read(nid, tenant_id=self.A, identity=self.me)
        self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
