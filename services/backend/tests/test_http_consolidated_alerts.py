import unittest
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import Header
from fastapi.testclient import TestClient
from psycopg.errors import QueryCanceled

from central_monitor import consolidated_alerts as alerts_module
from central_monitor import consolidated_tickets as tickets_module
from central_monitor.auth import Identity, current_identity
from central_monitor.main import app

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BASE = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
URL = "/api/v1/alerts/consolidated"
RANKS = {"disaster": 5, "high": 4, "average": 3, "warning": 2, "info": 1}


def identity_from_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(x_test_user, f"{x_test_user}@example.test", x_test_user)


def alert(tenant: str, number: int, minutes: int, severity: str = "warning", status: str = "open") -> dict:
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}", "tenant_id": tenant, "monitor_id": f"11111111-0000-4000-8000-{number:012d}",
        "title": f"Alerta {number}", "message": f"mensagem {number}", "severity": severity, "status": status,
        "opened_at": BASE + timedelta(minutes=minutes), "resolved_at": None,
        "monitor_name": f"Serviço {number}", "asset_id": "22222222-0000-4000-8000-000000000001", "asset_name": "Produção",
        "incident_id": None, "incident_status": None,
    }


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeCompanyDb:
    """One company: its grant, its alerts and the ORDER BY / cursor / LIMIT of the real query."""

    def __init__(self, rows=None, *, grant=None, fail=None):
        self.rows, self.grant, self.fail = rows or [], grant, fail
        self.calls: list[tuple[str, tuple]] = []

    def alert_queries(self):
        return [call for call in self.calls if "cm_alerts" in call[0]]

    def execute(self, query, params=()):
        sql, params = " ".join(str(query).split()), tuple(params)
        self.calls.append((sql, params))
        if "set_config('statement_timeout'" in sql:
            return Result()
        if "FROM cm_access_grants" in sql:
            return Result(row=self.grant)
        if self.fail:
            raise self.fail
        if "count(*) AS total" in sql:
            severities = [row["severity"] for row in self.rows]
            critical = sum(1 for value in severities if value in ("high", "disaster"))
            attention = sum(1 for value in severities if value in ("warning", "average"))
            return Result(row={"total": len(severities), "critical": critical, "attention": attention, "info": len(severities) - critical - attention})
        if "ORDER BY" in sql:
            rows = [{**row, "severity_rank": RANKS[row["severity"]]} for row in self.rows]
            if "ORDER BY CASE a.severity" in sql:
                key = lambda row: (row["severity_rank"], row["opened_at"], row["id"])
                if "< (%s::int" in sql:
                    after = (params[-4], params[-3], params[-2])
                    rows = [row for row in rows if key(row) < after]
            else:
                key = lambda row: (row["opened_at"], row["id"])
                if "< (%s::timestamptz" in sql:
                    after = (params[-3], params[-2])
                    rows = [row for row in rows if key(row) < after]
            return Result(rows=sorted(rows, key=key, reverse=True)[: params[-1]])
        raise AssertionError(f"unexpected SQL: {sql}")


def grant(role="operator"):
    return {"role": role, "row_scope": "company", "engagement_id": None, "department_id": None, "team_id": None}


class Base(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = identity_from_header
        self.client = TestClient(app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)

    def environment(self, dbs, *, master=False, user=None):
        names = {A: "Chaveou", B: "Morada", C: "JRI"}
        listing = MagicMock()
        listing.execute.return_value = Result(rows=[{"id": key, "name": names[key]} for key in sorted(names, key=lambda key: names[key])])
        context = MagicMock()
        context.__enter__.return_value = listing

        @contextmanager
        def scoped(tenant_id):
            yield dbs[tenant_id]

        user = user or {"id": "user-1", "active": True, "is_master": master}
        self.stack.enter_context(patch.object(tickets_module, "_ensure_user", return_value=user))
        self.stack.enter_context(patch.object(tickets_module, "connect", return_value=context))
        self.stack.enter_context(patch.object(tickets_module, "tenant_connection", side_effect=scoped))
        self.stack.enter_context(patch.object(alerts_module, "tenant_connection", side_effect=scoped))

    def get(self, **params):
        return self.client.get(URL, params=params, headers={"X-Test-User": "user-1"})

    def three_companies(self):
        dbs = {
            A: FakeCompanyDb([alert(A, 1, 10, "high"), alert(A, 2, 50, "warning"), alert(A, 3, 60, "info")], grant=grant()),
            B: FakeCompanyDb([alert(B, 4, 5, "disaster"), alert(B, 5, 10, "high"), alert(B, 6, 50, "warning")], grant=grant()),
            C: FakeCompanyDb([], grant=grant()),
        }
        self.environment(dbs)
        return dbs


def ids(body):
    return [int(item["id"][-12:]) for item in body["items"]]


class OrderingTests(Base):
    def test_severity_first_then_recency_then_id(self):
        self.three_companies()
        body = self.get(page_size=10).json()
        # disaster; two highs with the same time (higher id first); two warnings with the same time; info last
        self.assertEqual(ids(body), [4, 5, 1, 6, 2, 3])
        self.assertEqual({item["tenant_name"] for item in body["items"]}, {"Chaveou", "Morada"})

    def test_recent_sort_ignores_severity(self):
        self.three_companies()
        self.assertEqual(ids(self.get(page_size=10, sort="recent").json()), [3, 6, 2, 5, 1, 4])

    def test_item_shape_group_and_links(self):
        self.three_companies()
        body = self.get(page_size=10).json()
        first = body["items"][0]
        self.assertEqual((first["severity"], first["severity_group"]), ("disaster", "critical"))
        groups = {item["severity"]: item["severity_group"] for item in body["items"]}
        self.assertEqual(groups, {"disaster": "critical", "high": "critical", "warning": "attention", "info": "info"})
        for key in ("monitor_name", "asset_name", "incident_id", "incident_status", "message", "opened_at"):
            self.assertIn(key, first)
        self.assertNotIn("severity_rank", first)
        self.assertTrue(body["read_only"])
        self.assertEqual(body["sync_state"], "projected")
        self.assertFalse(body["partial"])


class CursorTests(Base):
    def walk(self, **params):
        seen, cursor, pages = [], None, 0
        while True:
            query = {"page_size": 2, **params, **({"cursor": cursor} if cursor else {})}
            body = self.get(**query).json()
            pages += 1
            seen += ids(body)
            cursor = body["next_cursor"]
            if not cursor:
                return seen, pages

    def test_cursor_walks_every_page_in_severity_order_without_duplicates(self):
        self.three_companies()
        seen, pages = self.walk()
        self.assertEqual(seen, [4, 5, 1, 6, 2, 3])
        self.assertEqual(pages, 3)

    def test_cursor_walks_every_page_in_recent_order_without_duplicates(self):
        self.three_companies()
        seen, _ = self.walk(sort="recent")
        self.assertEqual(seen, [3, 6, 2, 5, 1, 4])

    def test_no_next_cursor_when_everything_fits(self):
        self.three_companies()
        self.assertIsNone(self.get(page_size=10).json()["next_cursor"])


class TotalsTests(Base):
    def test_totals_and_counts_only_on_the_first_page(self):
        self.three_companies()
        first = self.get(page_size=2).json()
        self.assertEqual(first["total"], 6)
        self.assertEqual(first["counts"], {"critical": 3, "attention": 2, "info": 1})
        by_tenant = {row["tenant_name"]: row for row in first["totals_by_tenant"]}
        self.assertEqual((by_tenant["Chaveou"]["total"], by_tenant["Chaveou"]["critical"]), (3, 1))
        self.assertEqual((by_tenant["Morada"]["total"], by_tenant["Morada"]["critical"]), (3, 2))
        second = self.get(page_size=2, cursor=first["next_cursor"]).json()
        self.assertIsNone(second["total"])
        self.assertIsNone(second["counts"])
        self.assertEqual(second["totals_by_tenant"], [])


class FilterTests(Base):
    def calls(self, **params):
        dbs = self.three_companies()
        self.assertEqual(self.get(**params).status_code, 200)
        return dbs[A].alert_queries()

    def test_default_status_is_open(self):
        for sql, params in self.calls():
            self.assertIn("a.status=%s", sql)
            self.assertIn("open", params)

    def test_status_all_has_no_status_clause_and_resolved_is_passed(self):
        self.assertTrue(all("a.status=%s" not in sql for sql, _ in self.calls(status="all")))
        self.assertTrue(all("resolved" in params for _, params in self.calls(status="resolved")))

    def test_severity_filter_search_environment_and_window_reach_the_query(self):
        asset = "22222222-0000-4000-8000-000000000001"
        sql, params = self.calls(severity="high,disaster", q="api", asset_id=asset, **{"from": "2026-10-12T00:00:00Z", "to": "2026-10-13T00:00:00Z"})[0]
        self.assertIn("a.severity = ANY(%s)", sql)
        self.assertIn(["high", "disaster"], params)
        self.assertIn("%api%", params)
        self.assertIn("m.asset_id=%s", sql)
        self.assertIn(asset, params)
        self.assertIn("a.opened_at>=%s", sql)
        self.assertIn("a.opened_at<=%s", sql)
        self.assertIn(datetime(2026, 10, 12, tzinfo=UTC), params)

    def test_every_query_is_scoped_to_its_company_and_ignores_inactive_alerts(self):
        dbs = self.three_companies()
        self.get()
        for tenant in (A, B):
            for sql, params in dbs[tenant].alert_queries():
                self.assertIn("a.tenant_id=%s", sql)
                self.assertIn("a.inactive_at IS NULL", sql)
                self.assertEqual(params[0], tenant)

    def test_invalid_filters_are_rejected(self):
        self.three_companies()
        self.assertEqual(self.get(severity="catastrofe").status_code, 422)
        self.assertEqual(self.get(status="qualquer").status_code, 422)
        self.assertEqual(self.get(sort="abc").status_code, 422)
        self.assertEqual(self.get(asset_id="nao-e-uuid").status_code, 422)


class IsolationTests(Base):
    def test_company_without_grant_is_never_queried_even_if_requested(self):
        dbs = {A: FakeCompanyDb([alert(A, 1, 1)], grant=grant()), B: FakeCompanyDb([alert(B, 2, 2)], grant=grant()), C: FakeCompanyDb([alert(C, 3, 3)], grant=None)}
        self.environment(dbs)
        body = self.get(tenant_ids=f"{A},{C}").json()
        self.assertEqual({item["tenant_id"] for item in body["items"]}, {A})
        self.assertEqual(dbs[C].alert_queries(), [])
        self.assertEqual(dbs[B].calls, [])

    def test_x_tenant_id_header_is_ignored(self):
        dbs = {A: FakeCompanyDb([alert(A, 1, 1)], grant=grant()), B: FakeCompanyDb([alert(B, 2, 2)], grant=grant()), C: FakeCompanyDb([alert(C, 3, 3)], grant=None)}
        self.environment(dbs)
        response = self.client.get(URL, headers={"X-Test-User": "user-1", "X-Tenant-ID": C})
        self.assertEqual({item["tenant_id"] for item in response.json()["items"]}, {A, B})

    def test_client_role_does_not_receive_infrastructure_alerts(self):
        dbs = {A: FakeCompanyDb([alert(A, 1, 1)], grant=grant("client")), B: FakeCompanyDb([alert(B, 2, 2)], grant=grant("viewer")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        body = self.get().json()
        self.assertEqual({item["tenant_id"] for item in body["items"]}, {B})
        self.assertEqual(dbs[A].alert_queries(), [])

    def test_master_reads_every_active_company(self):
        dbs = {key: FakeCompanyDb([alert(key, index, index)], grant=None) for index, key in enumerate((A, B, C), 1)}
        self.environment(dbs, master=True)
        self.assertEqual({item["tenant_id"] for item in self.get().json()["items"]}, {A, B, C})

    def test_no_authorized_company_returns_an_empty_list(self):
        self.environment({A: FakeCompanyDb(grant=None), B: FakeCompanyDb(grant=None), C: FakeCompanyDb(grant=None)})
        body = self.get().json()
        self.assertEqual((body["items"], body["total"], body["partial"]), ([], 0, False))


class PartialFailureTests(Base):
    def test_one_company_failing_returns_partial_without_leaking_the_error(self):
        dbs = {A: FakeCompanyDb([alert(A, 1, 1)], grant=grant()), B: FakeCompanyDb(grant=grant(), fail=RuntimeError("secret-db-detail")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        response = self.get()
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["partial"])
        self.assertEqual([item["tenant_id"] for item in body["items"]], [A])
        self.assertEqual(body["tenants_failed"], [{"tenant_id": B, "tenant_name": "Morada", "code": "error", "retryable": False}])
        self.assertNotIn("secret-db-detail", response.text)

    def test_timeout_is_retryable_and_every_company_failing_is_a_503(self):
        dbs = {A: FakeCompanyDb([alert(A, 1, 1)], grant=grant()), B: FakeCompanyDb(grant=grant(), fail=QueryCanceled("slow")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        failed = self.get().json()["tenants_failed"][0]
        self.assertEqual((failed["code"], failed["retryable"]), ("timeout", True))
        dbs[A].fail = RuntimeError("down")
        self.assertEqual(self.get().status_code, 503)


class ValidationTests(Base):
    def setUp(self):
        super().setUp()
        self.three_companies()

    def test_page_size_and_tenant_ids_limits(self):
        self.assertEqual(self.get(page_size=101).status_code, 422)
        self.assertEqual(self.get(tenant_ids="x").status_code, 422)
        too_many = ",".join(f"{index:08d}-0000-4000-8000-000000000000" for index in range(21))
        self.assertEqual(self.get(tenant_ids=too_many).status_code, 422)

    def test_invalid_cursor_is_rejected(self):
        self.assertEqual(self.get(cursor="@@@").status_code, 422)
        self.assertEqual(self.get(cursor="e30").status_code, 422)

    def test_cursor_from_other_filters_sort_or_companies_is_rejected(self):
        cursor = self.get(page_size=1).json()["next_cursor"]
        self.assertIsNotNone(cursor)
        self.assertEqual(self.get(page_size=1, cursor=cursor).status_code, 200)
        self.assertEqual(self.get(page_size=1, cursor=cursor, q="x").status_code, 422)
        self.assertEqual(self.get(page_size=1, cursor=cursor, severity="high").status_code, 422)
        self.assertEqual(self.get(page_size=1, cursor=cursor, sort="recent").status_code, 422)
        self.assertEqual(self.get(page_size=1, cursor=cursor, tenant_ids=A).status_code, 422)


class RoutingAndReadOnlyTests(unittest.TestCase):
    def test_only_a_get_route_exists_under_the_consolidated_prefix(self):
        paths = {path: operations for path, operations in app.openapi()["paths"].items() if path.startswith(URL)}
        self.assertEqual(set(paths), {URL})
        self.assertEqual(set(paths[URL]), {"get"})

    def test_existing_alert_routes_are_untouched(self):
        paths = app.openapi()["paths"]
        self.assertIn("get", paths["/api/v1/alerts"])
        self.assertTrue({"put", "delete"} <= set(paths["/api/v1/alerts/{alert_id}"]))

    def test_severity_groups_match_the_dashboard(self):
        self.assertEqual({sev: alerts_module.severity_group(sev) for sev in alerts_module.SEVERITIES},
                         {"info": "info", "warning": "attention", "average": "attention", "high": "critical", "disaster": "critical"})

    def test_cursor_round_trip(self):
        digest = tickets_module._filters_digest({"q": None}, [A])
        cursor = alerts_module.encode_cursor("severity", 4, BASE, "00000000-0000-4000-8000-000000000001", digest)
        self.assertEqual(alerts_module.decode_cursor(cursor, "severity", digest), (4, BASE, "00000000-0000-4000-8000-000000000001"))


if __name__ == "__main__":
    unittest.main()
