import unittest
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import Header
from fastapi.testclient import TestClient
from psycopg.errors import QueryCanceled

from central_monitor import consolidated_inventory as inventory
from central_monitor import consolidated_tickets as tickets_module
from central_monitor.auth import Identity, current_identity
from central_monitor.main import app

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ASSETS_URL = "/api/v1/assets/consolidated"
MONITORS_URL = "/api/v1/monitors/consolidated"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SENSITIVE = ("configuration", "metadata", "address")
ZERO = {"up": 0, "down": 0, "unknown": 0, "inactive": 0}


def identity_from_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(x_test_user, f"{x_test_user}@example.test", x_test_user)


def fresh():
    return datetime.now(UTC) - timedelta(seconds=30)


def old():
    return datetime.now(UTC) - timedelta(hours=2)


def asset(number, name, *, active=True, kind="api"):
    return {"id": f"aaaa0000-0000-4000-8000-{number:012d}", "name": name, "kind": kind, "criticality": "high", "active": active}


def monitor(number, name, asset_row, *, last_status="up", checked=None, active=True, interval=60, message=None, mtype="http"):
    return {
        "id": f"bbbb0000-0000-4000-8000-{number:012d}", "name": name, "monitor_type": mtype, "active": active, "severity": "high",
        "last_status": last_status, "last_checked_at": checked if checked is not None else fresh(), "last_message": message,
        "interval_seconds": interval, "asset_id": asset_row["id"], "asset_name": asset_row["name"], "responsible_team_name": "Infra",
    }


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeCompanyDb:
    def __init__(self, *, grant=None, assets=None, monitors=None, alerts_by_asset=None, alerts_by_monitor=None, fail=None):
        self.grant, self.assets, self.monitors, self.fail = grant, assets or [], monitors or [], fail
        self.alerts_by_asset, self.alerts_by_monitor = alerts_by_asset or {}, alerts_by_monitor or {}
        self.calls: list[tuple[str, tuple]] = []

    def inventory_queries(self):
        return [call for call in self.calls if "cm_assets" in call[0] or "cm_monitors" in call[0] or "cm_alerts" in call[0]]

    def execute(self, query, params=()):
        sql, params = " ".join(str(query).split()), tuple(params)
        self.calls.append((sql, params))
        if "set_config('statement_timeout'" in sql:
            return Result()
        if "FROM cm_access_grants" in sql:
            return Result(row=self.grant)
        if self.fail:
            raise self.fail
        if "FROM cm_alerts" in sql:
            if "GROUP BY m.asset_id" in sql:
                return Result(rows=[{"asset_id": key, **value} for key, value in self.alerts_by_asset.items()])
            return Result(rows=[{"monitor_id": key, **value} for key, value in self.alerts_by_monitor.items()])
        if "FROM cm_assets a WHERE" in sql:
            return Result(rows=self.assets[: params[-1]])
        if "FROM cm_monitors m JOIN cm_assets a" in sql:
            return Result(rows=self.monitors[: params[-1]])
        if "FROM cm_monitors m WHERE m.tenant_id=%s" in sql:
            keys = ("asset_id", "active", "last_status", "last_checked_at", "interval_seconds")
            return Result(rows=[{key: row[key] for key in keys} for row in self.monitors])
        raise AssertionError(f"unexpected SQL: {sql}")


def grant(role="operator"):
    return {"role": role, "row_scope": "company", "engagement_id": None, "department_id": None, "team_id": None}


def alerts(critical=0, attention=0, info=0):
    return {"critical": critical, "attention": attention, "info": info}


class PolicyTests(unittest.TestCase):
    def test_staleness_window_is_three_intervals_with_a_floor(self):
        self.assertTrue(inventory.is_stale(None, 60, NOW))
        self.assertFalse(inventory.is_stale(NOW - timedelta(seconds=179), 60, NOW))
        self.assertTrue(inventory.is_stale(NOW - timedelta(seconds=181), 60, NOW))
        self.assertFalse(inventory.is_stale(NOW - timedelta(seconds=1000), 600, NOW))  # 3 x 600 = 1800
        self.assertTrue(inventory.is_stale(NOW - timedelta(seconds=1900), 600, NOW))
        self.assertFalse(inventory.is_stale(NOW - timedelta(seconds=170), 10, NOW))  # floor of 180 s

    def test_service_state_precedence(self):
        def state(**changes):
            row = {"active": True, "last_status": "up", "last_checked_at": NOW - timedelta(seconds=30), "interval_seconds": 60, **changes}
            return inventory.service_state(row, NOW)

        self.assertEqual(state(), "healthy")
        self.assertEqual(state(last_status="down"), "unavailable")
        self.assertEqual(state(active=False, last_status="down"), "inactive")
        self.assertEqual(state(last_status="unknown"), "unknown")
        self.assertEqual(state(last_status="qualquer"), "unknown")
        self.assertEqual(state(last_checked_at=None), "unknown")

    def test_unknown_or_stale_never_counts_as_healthy(self):
        old_check = NOW - timedelta(hours=1)
        self.assertEqual(inventory.service_state({"active": True, "last_status": "up", "last_checked_at": old_check, "interval_seconds": 60}, NOW), "unknown")
        self.assertEqual(inventory.service_state({"active": True, "last_status": "down", "last_checked_at": old_check, "interval_seconds": 60}, NOW), "unknown")

    def test_environment_status_precedence(self):
        status = lambda active=True, up=0, down=0, unknown=0, inactive=0, critical=0, attention=0: inventory.environment_status(
            active=active, services={"up": up, "down": down, "unknown": unknown, "inactive": inactive}, alerts={"critical": critical, "attention": attention})
        self.assertEqual(status(up=3), "healthy")
        self.assertEqual(status(active=False, up=3, down=1, critical=2), "inactive")
        self.assertEqual(status(up=2, down=1, critical=3), "unavailable")
        self.assertEqual(status(up=2, critical=1, attention=4), "critical")
        self.assertEqual(status(up=2, attention=1), "attention")
        self.assertEqual(status(), "unknown")
        self.assertEqual(status(inactive=3), "unknown")
        self.assertEqual(status(up=2, unknown=1), "unknown")
        self.assertEqual(status(unknown=1, critical=1), "critical")  # open alerts outrank unknown telemetry


class Base(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = identity_from_header
        self.client = TestClient(app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)

    def environment(self, dbs, *, master=False):
        names = {A: "Chaveou", B: "Morada", C: "JRI"}
        listing = MagicMock()
        listing.execute.return_value = Result(rows=[{"id": key, "name": names[key]} for key in sorted(names, key=lambda key: names[key])])
        context = MagicMock()
        context.__enter__.return_value = listing

        @contextmanager
        def scoped(tenant_id):
            yield dbs[tenant_id]

        user = {"id": "user-1", "active": True, "is_master": master}
        self.stack.enter_context(patch.object(tickets_module, "_ensure_user", return_value=user))
        self.stack.enter_context(patch.object(tickets_module, "connect", return_value=context))
        self.stack.enter_context(patch.object(tickets_module, "tenant_connection", side_effect=scoped))
        self.stack.enter_context(patch.object(inventory, "tenant_connection", side_effect=scoped))

    def get(self, url, **params):
        return self.client.get(url, params=params, headers={"X-Test-User": "user-1"})


def names(body):
    return [item["name"] for item in body["items"]]


class EnvironmentTests(Base):
    def companies(self):
        api, db, site = asset(1, "API"), asset(2, "Banco"), asset(3, "Site")
        a = FakeCompanyDb(grant=grant(), assets=[api, db, asset(4, "Antigo", active=False)],
                          monitors=[monitor(1, "api-health", api), monitor(2, "db-tcp", db, last_status="down")],
                          alerts_by_asset={db["id"]: alerts(critical=1, attention=2, info=1)})
        b = FakeCompanyDb(grant=grant(), assets=[site], monitors=[monitor(3, "site", site, last_status="up", checked=old())],
                          alerts_by_asset={})
        dbs = {A: a, B: b, C: FakeCompanyDb(grant=grant())}
        self.environment(dbs)
        return dbs

    def test_status_is_computed_and_problems_come_first(self):
        self.companies()
        body = self.get(ASSETS_URL, page_size=10).json()
        by_name = {item["name"]: item for item in body["items"]}
        self.assertEqual(by_name["Banco"]["status"], "unavailable")
        self.assertEqual(by_name["API"]["status"], "healthy")
        self.assertEqual(by_name["Site"]["status"], "unknown")  # up, but telemetry is stale
        self.assertEqual(by_name["Antigo"]["status"], "inactive")
        self.assertEqual(names(body), ["Banco", "Site", "API", "Antigo"])
        self.assertEqual(by_name["Banco"]["services"], {"up": 0, "down": 1, "unknown": 0, "inactive": 0, "total": 1})
        self.assertEqual(by_name["Banco"]["alerts"], alerts(1, 2, 1))
        self.assertEqual(by_name["Site"]["tenant_name"], "Morada")
        self.assertTrue(body["read_only"])

    def test_counts_total_and_pages_are_exact_and_follow_the_filter(self):
        self.companies()
        body = self.get(ASSETS_URL, page_size=3).json()
        self.assertEqual((body["total"], body["pages"], body["page"], len(body["items"])), (4, 2, 1, 3))
        self.assertEqual(body["counts"], {"healthy": 1, "attention": 0, "critical": 0, "unavailable": 1, "unknown": 1, "inactive": 1})
        second = self.get(ASSETS_URL, page_size=3, page=2).json()
        self.assertEqual(names(second), ["Antigo"])
        filtered = self.get(ASSETS_URL, status="unavailable,unknown").json()
        self.assertEqual(filtered["total"], 2)
        self.assertEqual(filtered["counts"]["healthy"], 0)
        self.assertEqual(filtered["counts"]["unavailable"], 1)
        self.assertEqual({row["tenant_name"]: row["total"] for row in filtered["totals_by_tenant"]}, {"Chaveou": 1, "Morada": 1, "JRI": 0})

    def test_sort_by_name(self):
        self.companies()
        self.assertEqual(names(self.get(ASSETS_URL, sort="name").json()), ["Antigo", "API", "Banco", "Site"])

    def test_kind_and_search_reach_the_query_and_invalid_values_are_rejected(self):
        dbs = self.companies()
        self.assertEqual(self.get(ASSETS_URL, kind="api,database", q="ban").status_code, 200)
        sql, params = next(call for call in dbs[A].calls if "FROM cm_assets a WHERE" in call[0])
        self.assertIn("a.kind = ANY(%s)", sql)
        self.assertIn(["api", "database"], params)
        self.assertIn("a.name ILIKE %s", sql)
        self.assertIn("%ban%", params)
        for bad in ({"status": "feliz"}, {"kind": "geladeira"}, {"sort": "x"}, {"page": 0}, {"page_size": 101}):
            self.assertEqual(self.get(ASSETS_URL, **bad).status_code, 422, bad)

    def test_minimal_projection_never_exposes_configuration_metadata_or_address(self):
        dbs = self.companies()
        response = self.get(ASSETS_URL, page_size=10)
        for db in (dbs[A], dbs[B]):
            for sql, _ in db.inventory_queries():
                for column in SENSITIVE:
                    self.assertNotIn(column, sql)
        for column in SENSITIVE:
            self.assertNotIn(column, response.text)
        self.assertEqual(set(response.json()["items"][0]), {"id", "name", "kind", "criticality", "active", "status", "services", "alerts", "tenant_id", "tenant_name"})

    def test_only_open_active_alerts_are_counted_per_company(self):
        dbs = self.companies()
        self.get(ASSETS_URL)
        sql, params = next(call for call in dbs[A].calls if "FROM cm_alerts" in call[0])
        self.assertIn("a.status='open'", sql)
        self.assertIn("a.inactive_at IS NULL", sql)
        self.assertEqual(params, (A,))

    def test_a_company_over_the_safety_limit_is_reported_as_truncated(self):
        self.companies()
        with patch.object(inventory, "MAX_ROWS_PER_TENANT", 2):
            body = self.get(ASSETS_URL).json()
        self.assertEqual(body["tenants_truncated"], [{"tenant_id": A, "tenant_name": "Chaveou"}])
        self.assertEqual(body["total"], 3)  # 2 of Chaveou + 1 of Morada


class ServiceTests(Base):
    def companies(self):
        prod = asset(1, "Produção")
        a = FakeCompanyDb(grant=grant(), assets=[prod], monitors=[
            monitor(1, "api", prod, last_status="up"),
            monitor(2, "banco", prod, last_status="down", message="x" * 500),
            monitor(3, "fila", prod, last_status="up", checked=old()),
            monitor(4, "legado", prod, active=False),
        ], alerts_by_monitor={f"bbbb0000-0000-4000-8000-{2:012d}": alerts(critical=2)})
        b = FakeCompanyDb(grant=grant(), assets=[asset(9, "Site")], monitors=[monitor(5, "site", asset(9, "Site"), last_status="unknown")])
        self.environment({A: a, B: b, C: FakeCompanyDb(grant=grant())})
        return a, b

    def test_state_stale_and_problem_first_order(self):
        self.companies()
        body = self.get(MONITORS_URL, page_size=10).json()
        by_name = {item["name"]: item for item in body["items"]}
        self.assertEqual({key: value["state"] for key, value in by_name.items()}, {"api": "healthy", "banco": "unavailable", "fila": "unknown", "legado": "inactive", "site": "unknown"})
        self.assertTrue(by_name["fila"]["stale"])
        self.assertFalse(by_name["api"]["stale"])
        self.assertFalse(by_name["legado"]["stale"])  # inactive services are not "stale"
        self.assertEqual(names(body), ["banco", "fila", "site", "api", "legado"])
        self.assertEqual(by_name["banco"]["open_alerts"], alerts(critical=2))
        self.assertEqual(by_name["api"]["open_alerts"], alerts())
        self.assertEqual((by_name["api"]["asset_name"], by_name["api"]["tenant_name"]), ("Produção", "Chaveou"))
        self.assertEqual(body["counts"], {"healthy": 1, "unavailable": 1, "unknown": 2, "inactive": 1})

    def test_last_message_is_truncated(self):
        self.companies()
        item = next(i for i in self.get(MONITORS_URL, page_size=10).json()["items"] if i["name"] == "banco")
        self.assertEqual(len(item["last_message"]), inventory.LAST_MESSAGE_LIMIT)

    def test_state_filter_pagination_and_sort(self):
        self.companies()
        problems = self.get(MONITORS_URL, state="unavailable,unknown", page_size=2).json()
        self.assertEqual((problems["total"], problems["pages"]), (3, 2))
        self.assertEqual(problems["counts"], {"healthy": 0, "unavailable": 1, "unknown": 2, "inactive": 0})
        self.assertEqual(names(self.get(MONITORS_URL, sort="name", page_size=10).json()), ["api", "banco", "fila", "legado", "site"])

    def test_filters_reach_the_query_and_invalid_values_are_rejected(self):
        a, _ = self.companies()
        asset_id = "aaaa0000-0000-4000-8000-000000000001"
        self.assertEqual(self.get(MONITORS_URL, asset_id=asset_id, monitor_type="tcp", q="ban").status_code, 200)
        sql, params = next(call for call in a.calls if "FROM cm_monitors m JOIN cm_assets a" in call[0])
        for fragment in ("m.asset_id=%s", "m.monitor_type=%s", "m.name ILIKE %s"):
            self.assertIn(fragment, sql)
        for value in (asset_id, "tcp", "%ban%"):
            self.assertIn(value, params)
        for bad in ({"state": "feliz"}, {"asset_id": "x"}, {"sort": "x"}, {"monitor_type": "x" * 41}):
            self.assertEqual(self.get(MONITORS_URL, **bad).status_code, 422, bad)

    def test_minimal_projection_never_exposes_configuration(self):
        a, b = self.companies()
        response = self.get(MONITORS_URL, page_size=10)
        for db in (a, b):
            for sql, _ in db.inventory_queries():
                for column in SENSITIVE:
                    self.assertNotIn(column, sql)
        for column in SENSITIVE:
            self.assertNotIn(column, response.text)
        expected = {"id", "name", "monitor_type", "active", "severity", "state", "last_status", "stale", "last_checked_at", "last_message", "interval_seconds",
                    "asset_id", "asset_name", "responsible_team_name", "open_alerts", "tenant_id", "tenant_name"}
        self.assertEqual(set(response.json()["items"][0]), expected)


class IsolationAndFailureTests(Base):
    def both(self, url):
        yield url

    def test_company_without_grant_is_never_queried_and_client_role_is_excluded(self):
        prod = asset(1, "Produção")
        dbs = {A: FakeCompanyDb(grant=grant(), assets=[prod], monitors=[monitor(1, "api", prod)]),
               B: FakeCompanyDb(grant=grant("client"), assets=[prod], monitors=[monitor(2, "x", prod)]),
               C: FakeCompanyDb(grant=None, assets=[prod], monitors=[monitor(3, "y", prod)])}
        self.environment(dbs)
        for url in (ASSETS_URL, MONITORS_URL):
            body = self.get(url, tenant_ids=f"{A},{B},{C}").json()
            self.assertEqual({item["tenant_id"] for item in body["items"]}, {A}, url)
        self.assertEqual(dbs[B].inventory_queries(), [])
        self.assertEqual(dbs[C].inventory_queries(), [])

    def test_x_tenant_id_is_ignored_and_master_reads_everything(self):
        prod = asset(1, "Produção")
        dbs = {key: FakeCompanyDb(grant=None, assets=[prod], monitors=[monitor(index, f"m{index}", prod)]) for index, key in enumerate((A, B, C), 1)}
        self.environment(dbs, master=True)
        response = self.client.get(MONITORS_URL, headers={"X-Test-User": "user-1", "X-Tenant-ID": C})
        self.assertEqual({item["tenant_id"] for item in response.json()["items"]}, {A, B, C})

    def test_partial_failure_timeout_and_total_failure(self):
        prod = asset(1, "Produção")
        dbs = {A: FakeCompanyDb(grant=grant(), assets=[prod], monitors=[monitor(1, "api", prod)]),
               B: FakeCompanyDb(grant=grant(), fail=RuntimeError("secret-db-detail")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        for url in (ASSETS_URL, MONITORS_URL):
            response = self.get(url)
            body = response.json()
            self.assertEqual(response.status_code, 200, url)
            self.assertTrue(body["partial"])
            self.assertEqual(body["tenants_failed"], [{"tenant_id": B, "tenant_name": "Morada", "code": "error", "retryable": False}])
            self.assertNotIn("secret-db-detail", response.text)
        dbs[B].fail = QueryCanceled("slow")
        self.assertEqual(self.get(ASSETS_URL).json()["tenants_failed"][0]["code"], "timeout")
        dbs[A].fail = RuntimeError("down")
        self.assertEqual(self.get(ASSETS_URL).status_code, 503)
        self.assertEqual(self.get(MONITORS_URL).status_code, 503)

    def test_no_authorized_company_returns_an_empty_list(self):
        self.environment({A: FakeCompanyDb(grant=None), B: FakeCompanyDb(grant=None), C: FakeCompanyDb(grant=None)})
        body = self.get(ASSETS_URL).json()
        self.assertEqual((body["items"], body["total"], body["pages"], body["partial"]), ([], 0, 1, False))


class RoutingTests(unittest.TestCase):
    def test_only_get_routes_exist_and_existing_routes_are_untouched(self):
        paths = app.openapi()["paths"]
        for prefix in (ASSETS_URL, MONITORS_URL):
            self.assertEqual({path for path in paths if path.startswith(prefix)}, {prefix})
            self.assertEqual(set(paths[prefix]), {"get"})
        self.assertIn("get", paths["/api/v1/assets"])
        self.assertIn("get", paths["/api/v1/monitors"])
        self.assertTrue({"put", "delete"} <= set(paths["/api/v1/assets/{asset_id}"]))
        self.assertTrue({"put", "delete"} <= set(paths["/api/v1/monitors/{monitor_id}"]))


if __name__ == "__main__":
    unittest.main()
