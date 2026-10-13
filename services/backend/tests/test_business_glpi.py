import unittest
from unittest import mock

try:
    from central_monitor import business_glpi
    from central_monitor.glpi import GlpiClient
except ImportError:  # backend dependencies are not installed
    GlpiClient = None


def client():
    c = GlpiClient.__new__(GlpiClient)
    c.url, c.app_token, c.user_token, c.username, c.password = "http://glpi", "", "t", "", ""
    return c


OCCURRENCE = {"id": "11111111-2222-3333-4444-555555555555", "severity": "critico", "tenant_name": "Empresa A",
              "indicator_name": "Pedidos pendentes", "indicator_code": "pedidos_pendentes",
              "marker": "NXBM-1111111122223333"}


@unittest.skipIf(GlpiClient is None, "backend dependencies not installed")
class BusinessGlpiTests(unittest.TestCase):
    def setUp(self):
        GlpiClient._category_cache.clear()

    def test_existing_category_is_reused_and_not_recreated(self):
        c = client()
        with mock.patch.object(GlpiClient, "_request", return_value=[{"id": 7, "name": "Outra"}, {"id": 9, "name": "Negócio"}]) as req:
            self.assertEqual(c.ensure_category(), 9)
            self.assertEqual(c.ensure_category(), 9)  # cached
        self.assertEqual(req.call_count, 1)

    def test_missing_category_is_created_once(self):
        c = client()
        calls = []

        def fake(method, path, payload=None, params=None):
            calls.append((method, path))
            return [] if method == "GET" else {"id": 15}

        with mock.patch.object(GlpiClient, "_request", side_effect=fake):
            self.assertEqual(c.ensure_category(), 15)
        self.assertEqual(calls, [("GET", "ITILCategory"), ("POST", "ITILCategory")])

    def test_ticket_uses_business_category_and_states_values_stay_in_nexora(self):
        c = client()
        sent = {}

        def fake(method, path, payload=None, params=None):
            if path == "search/Ticket":
                return {"data": []}
            if path == "ITILCategory":
                return [{"id": 9, "name": "Negócio"}]
            sent.update(payload["input"])
            return {"id": 321}

        with mock.patch.object(GlpiClient, "_request", side_effect=fake):
            self.assertEqual(c.create_business_ticket(dict(OCCURRENCE)), 321)
        self.assertEqual(sent["itilcategories_id"], 9)
        self.assertEqual(sent["urgency"], 5)
        self.assertIn(OCCURRENCE["marker"], sent["name"])
        self.assertIn("permanecem no Nexora", sent["content"])

    def test_retry_after_lost_response_reuses_existing_ticket(self):
        c = client()
        with mock.patch.object(GlpiClient, "_request", return_value={"data": [{"2": 777}]}) as req:
            self.assertEqual(c.create_business_ticket(dict(OCCURRENCE)), 777)
        self.assertEqual(req.call_count, 1)  # only the search; no POST

    def test_marker_is_stable(self):
        self.assertEqual(business_glpi.marker_for(OCCURRENCE["id"]), "NXBM-1111111122223333")

    def test_normalization_followup_never_resolves_ticket(self):
        self.assertIn("não foi encerrado", business_glpi.FOLLOWUP["normalized"])


if __name__ == "__main__":
    unittest.main()
