import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from central_monitor.auth import Identity, current_identity
from central_monitor.command_bus import canonical_payload_hash
from central_monitor.main import app


TENANT="11111111-1111-1111-1111-111111111111"
EVENT={"eventId":"evt-100","source":"zabbix","assetExternalKey":"host-1","eventType":"problem","occurredAt":"2026-10-11T12:00:00Z","severity":"high","attributes":{"connection_key":"primary"}}


class Result:
    def __init__(self,row=None):self.row=row
    def fetchone(self):return self.row


class InboxConnection:
    def __init__(self,inserted=True,existing=None):
        self.inserted=inserted;self.existing=existing;self.statements=[];self.committed=False
    def execute(self,query,params=()):
        sql=" ".join(str(query).split());self.statements.append((sql,params))
        if sql.startswith("INSERT INTO cm_integration_inbox"):
            return Result({"id":"inbox-1"} if self.inserted else None)
        if sql.startswith("SELECT id,payload_hash,state,result FROM cm_integration_inbox"):
            return Result(self.existing)
        return Result()
    def commit(self):self.committed=True


class HttpEventInboxTests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity]=lambda:Identity("operator","operator@example.test","Operator")
        self.client=TestClient(app)
    def tearDown(self):
        app.dependency_overrides.clear();self.client.close()
    @staticmethod
    @contextmanager
    def scoped(connection):yield connection

    def test_first_event_is_recorded_and_correlated_once(self):
        connection=InboxConnection(inserted=True)
        with patch("central_monitor.main.authorize_tenant",return_value={"id":"operator"}),patch("central_monitor.main.tenant_connection",return_value=self.scoped(connection)),patch("central_monitor.main.ingest_mapped_event",return_value={"status":"correlated","id":"event-1","episode_id":"episode-1"}) as ingest:
            response=self.client.post("/api/v1/events",headers={"X-Tenant-ID":TENANT},json=EVENT)
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()["inbox_id"],"inbox-1")
        ingest.assert_called_once()
        sql="\n".join(item[0] for item in connection.statements)
        self.assertIn("INSERT INTO cm_integration_inbox",sql)
        self.assertIn("UPDATE cm_integration_inbox SET state=%s",sql)

    def test_repeated_equivalent_event_returns_saved_result_without_processing(self):
        digest=canonical_payload_hash(EVENT)
        connection=InboxConnection(inserted=False,existing={"id":"inbox-1","payload_hash":digest,"state":"correlated","result":{"status":"correlated","id":"event-1"}})
        with patch("central_monitor.main.authorize_tenant",return_value={"id":"operator"}),patch("central_monitor.main.tenant_connection",return_value=self.scoped(connection)),patch("central_monitor.main.ingest_mapped_event") as ingest:
            response=self.client.post("/api/v1/events",headers={"X-Tenant-ID":TENANT},json=EVENT)
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()["status"],"duplicate")
        self.assertEqual(response.json()["original_state"],"correlated")
        ingest.assert_not_called()

    def test_reused_event_id_with_different_payload_is_rejected_and_persisted(self):
        connection=InboxConnection(inserted=False,existing={"id":"inbox-1","payload_hash":"different","state":"correlated","result":{}})
        with patch("central_monitor.main.authorize_tenant",return_value={"id":"operator"}),patch("central_monitor.main.tenant_connection",return_value=self.scoped(connection)),patch("central_monitor.main.ingest_mapped_event") as ingest:
            response=self.client.post("/api/v1/events",headers={"X-Tenant-ID":TENANT},json=EVENT)
        self.assertEqual(response.status_code,409)
        self.assertTrue(connection.committed)
        self.assertTrue(any("state='rejected'" in sql for sql,_ in connection.statements))
        ingest.assert_not_called()


if __name__=="__main__":unittest.main()
