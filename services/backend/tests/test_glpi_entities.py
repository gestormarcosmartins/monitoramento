import unittest
from contextlib import contextmanager
from unittest import mock

try:
    from central_monitor import glpi_entities
    from central_monitor.glpi import GlpiClient
except ImportError:  # backend dependencies are not installed
    GlpiClient = None


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDb:
    def __init__(self, tenant):
        self.tenant = tenant
        self.updates = []

    def execute(self, sql, params=()):
        if sql.startswith("UPDATE cm_tenants"):
            self.updates.append(params)
            self.tenant["glpi_entity_id"] = params[0]
            return Result()
        if sql.startswith("SELECT pg_advisory"):
            return Result()
        return Result(dict(self.tenant))


def fake_connect(db):
    @contextmanager
    def connect():
        yield db
    return connect


def client():
    c = GlpiClient.__new__(GlpiClient)
    c.url, c.app_token, c.user_token, c.username, c.password = "http://glpi", "", "t", "", ""
    return c


@unittest.skipIf(GlpiClient is None, "backend dependencies not installed")
class EntityTests(unittest.TestCase):
    def test_entity_created_once_and_stored_on_the_company(self):
        db = FakeDb({"name": "Morada", "glpi_entity_id": None})
        stub = mock.Mock(spec=GlpiClient)
        stub.find_entity.return_value = None
        stub.create_entity.return_value = 42
        with mock.patch.object(glpi_entities, "connect", fake_connect(db)):
            self.assertEqual(glpi_entities.ensure_tenant_entity("t-1", stub), 42)
            self.assertEqual(glpi_entities.ensure_tenant_entity("t-1", stub), 42)  # now cached in the DB
        stub.create_entity.assert_called_once_with("t-1", "Morada")
        self.assertEqual(db.updates, [(42, "t-1")])

    def test_existing_glpi_entity_is_adopted_not_duplicated(self):
        db = FakeDb({"name": "Morada", "glpi_entity_id": None})
        stub = mock.Mock(spec=GlpiClient)
        stub.find_entity.return_value = 7
        with mock.patch.object(glpi_entities, "connect", fake_connect(db)):
            self.assertEqual(glpi_entities.ensure_tenant_entity("t-1", stub), 7)
        stub.create_entity.assert_not_called()

    def test_glpi_failure_propagates_so_no_ticket_is_opened_without_company(self):
        db = FakeDb({"name": "Morada", "glpi_entity_id": None})
        stub = mock.Mock(spec=GlpiClient)
        stub.find_entity.side_effect = RuntimeError("glpi down")
        with mock.patch.object(glpi_entities, "connect", fake_connect(db)):
            with self.assertRaises(RuntimeError):
                glpi_entities.ensure_tenant_entity("t-1", stub)
        self.assertEqual(db.updates, [])

    def test_find_entity_matches_only_this_companys_tenant_id(self):
        listing = [{"id": 1, "name": "Morada", "comment": "Empresa do Nexora. nexora-tenant:other"},
                   {"id": 2, "name": "Morada", "comment": "Empresa do Nexora. nexora-tenant:t-1"}]
        with mock.patch.object(GlpiClient, "_request", return_value=listing):
            self.assertEqual(client().find_entity("t-1", "Morada"), 2)

    def test_same_name_is_disambiguated(self):
        sent = {}

        def fake(method, path, payload=None, params=None):
            if method == "GET":
                return [{"id": 1, "name": "Morada"}]
            sent.update(payload["input"])
            return {"id": 5}

        with mock.patch.object(GlpiClient, "_request", side_effect=fake):
            self.assertEqual(client().create_entity("12345678-aaaa", "Morada"), 5)
        self.assertEqual(sent["name"], "Morada (12345678)")
        self.assertIn("nexora-tenant:12345678-aaaa", sent["comment"])

    def test_technical_ticket_is_bound_to_company_entity(self):
        sent = {}

        def fake(method, path, payload=None, params=None):
            sent.update(payload["input"])
            return {"id": 9}

        incident = {"id": "i1", "tenant_id": "t-1", "title": "Falha", "severity": "high", "glpi_entity_id": 42}
        with mock.patch.object(GlpiClient, "_request", side_effect=fake):
            client().create_ticket(incident)
        self.assertEqual(sent["entities_id"], 42)

    def test_business_ticket_is_bound_to_company_entity_and_shared_category(self):
        sent = {}

        def fake(method, path, payload=None, params=None):
            if path == "search/Ticket":
                return {"data": []}
            if path == "ITILCategory":
                return [{"id": 3, "name": "Negócio"}]
            sent.update(payload["input"])
            return {"id": 9}

        GlpiClient._category_cache.clear()
        occurrence = {"id": "o1", "severity": "atencao", "tenant_name": "Morada", "indicator_name": "Pedidos",
                      "indicator_code": "pedidos", "marker": "NXBM-1", "glpi_entity_id": 42}
        with mock.patch.object(GlpiClient, "_request", side_effect=fake):
            client().create_business_ticket(occurrence)
        self.assertEqual((sent["entities_id"], sent["itilcategories_id"]), (42, 3))


if __name__ == "__main__":
    unittest.main()
