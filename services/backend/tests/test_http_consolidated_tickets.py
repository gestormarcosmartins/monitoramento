import unittest
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import Header, HTTPException
from fastapi.testclient import TestClient
from psycopg.errors import QueryCanceled

from central_monitor import consolidated_tickets as module
from central_monitor.auth import Identity, current_identity
from central_monitor.main import app

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BASE = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
URL = "/api/v1/tickets/consolidated"
SUMMARY_URL = URL + "/summary"


def identity_from_header(x_test_user: str = Header(alias="X-Test-User")) -> Identity:
    return Identity(x_test_user, f"{x_test_user}@example.test", x_test_user)


def ticket(tenant: str, number: int, minutes: int) -> dict:
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "tenant_id": tenant,
        "title": f"Chamado {number}",
        "status": "new",
        "priority": "medium",
        "updated_at": BASE + timedelta(minutes=minutes),
        "version": 1,
    }


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeCompanyDb:
    """Emulates one company: its grant, its tickets and the ORDER BY/cursor/LIMIT of the real query."""

    def __init__(self, rows=None, *, grant=None, fail=None, summary=None):
        self.rows, self.grant, self.fail, self.summary = rows or [], grant, fail, summary
        self.calls: list[tuple[str, tuple]] = []

    def incident_queries(self) -> list[tuple[str, tuple]]:
        return [call for call in self.calls if "cm_incidents" in call[0]]

    def execute(self, query, params=()):
        sql, params = " ".join(str(query).split()), tuple(params)
        self.calls.append((sql, params))
        if "set_config('statement_timeout'" in sql:
            return Result()
        if "FROM cm_access_grants" in sql:
            return Result(row=self.grant)
        if self.fail:
            raise self.fail
        if "FILTER(WHERE" in sql:
            return Result(row=self.summary)
        if "count(*) AS total" in sql:
            return Result(row={"total": len(self.rows)})
        if "ORDER BY i.updated_at DESC, i.id DESC" in sql:
            ordered = sorted(self.rows, key=lambda row: (row["updated_at"], row["id"]), reverse=True)
            if "(i.updated_at, i.id) <" in sql:
                after = (params[-3], params[-2])
                ordered = [row for row in ordered if (row["updated_at"], row["id"]) < after]
            return Result(rows=ordered[: params[-1]])
        raise AssertionError(f"unexpected SQL: {sql}")


def grant(role="operator", row_scope="company"):
    return {"role": role, "row_scope": row_scope, "engagement_id": None, "department_id": None, "team_id": None}


class ConsolidatedBase(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[current_identity] = identity_from_header
        self.client = TestClient(app)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)

    def environment(self, dbs: dict[str, FakeCompanyDb], *, names=None, user=None, master=False):
        """Patch the user, the company list and the per-company connections."""
        names = names or {A: "Chaveou", B: "Morada", C: "JRI"}
        user = user or {"id": "user-1", "active": True, "is_master": master}
        companies = Result(rows=[{"id": key, "name": names[key]} for key in sorted(names, key=lambda key: names[key])])
        listing = MagicMock()
        listing.execute.return_value = companies
        context = MagicMock()
        context.__enter__.return_value = listing
        opened: list[str] = []

        @contextmanager
        def scoped(tenant_id):
            opened.append(tenant_id)
            yield dbs[tenant_id]

        self.stack.enter_context(patch.object(module, "_ensure_user", return_value=user))
        self.stack.enter_context(patch.object(module, "connect", return_value=context))
        self.stack.enter_context(patch.object(module, "tenant_connection", side_effect=scoped))
        return opened

    def get(self, url=URL, **params):
        return self.client.get(url, params=params, headers={"X-Test-User": "user-1"})


class ListOrderingAndCursorTests(ConsolidatedBase):
    def test_merges_companies_by_recency_and_breaks_ties_by_id(self):
        dbs = {
            A: FakeCompanyDb([ticket(A, 1, 10), ticket(A, 3, 5), ticket(A, 9, 20)], grant=grant()),
            B: FakeCompanyDb([ticket(B, 4, 5)], grant=grant()),
            C: FakeCompanyDb([], grant=grant()),
        }
        self.environment(dbs)
        body = self.get(page_size=10).json()
        # same updated_at: the higher id wins, regardless of which company was merged first
        self.assertEqual([item["id"][-12:] for item in body["items"]], ["000000000009", "000000000001", "000000000004", "000000000003"])
        self.assertEqual({item["tenant_name"] for item in body["items"]}, {"Chaveou", "Morada"})
        self.assertTrue(body["read_only"])
        self.assertEqual(body["sync_state"], "projected")
        self.assertFalse(body["partial"])

    def test_cursor_walks_every_page_without_duplicates_or_gaps(self):
        rows = {A: [ticket(A, n, n % 4) for n in range(1, 5)], B: [ticket(B, n, n % 4) for n in range(11, 15)], C: [ticket(C, n, n % 4) for n in range(21, 24)]}
        dbs = {key: FakeCompanyDb(value, grant=grant()) for key, value in rows.items()}
        self.environment(dbs)
        everything = sorted((row for value in rows.values() for row in value), key=lambda row: (row["updated_at"], row["id"]), reverse=True)
        seen, cursor, pages = [], None, 0
        while True:
            params = {"page_size": 4}
            if cursor:
                params["cursor"] = cursor
            body = self.get(**params).json()
            pages += 1
            if pages == 1:
                self.assertEqual(body["total"], 11)
                self.assertEqual(sum(item["total"] for item in body["totals_by_tenant"]), 11)
            else:
                self.assertIsNone(body["total"])
                self.assertEqual(body["totals_by_tenant"], [])
            seen += [item["id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(pages, 3)
        self.assertEqual(seen, [row["id"] for row in everything])
        self.assertEqual(len(seen), len(set(seen)))

    def test_item_updated_between_pages_is_never_repeated(self):
        rows = {A: [ticket(A, n, n) for n in range(1, 7)], B: [ticket(B, n, n) for n in range(11, 17)]}
        dbs = {A: FakeCompanyDb(rows[A], grant=grant()), B: FakeCompanyDb(rows[B], grant=grant()), C: FakeCompanyDb([], grant=grant())}
        self.environment(dbs)
        first = self.get(page_size=5).json()
        rows[B][0]["updated_at"] = BASE + timedelta(minutes=500)  # an unseen ticket became the newest
        seen, cursor = [item["id"] for item in first["items"]], first["next_cursor"]
        while cursor:
            body = self.get(page_size=5, cursor=cursor).json()
            seen += [item["id"] for item in body["items"]]
            cursor = body["next_cursor"]
        self.assertEqual(len(seen), len(set(seen)))

    def test_no_next_cursor_when_everything_fits(self):
        self.environment({A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb([], grant=grant()), C: FakeCompanyDb([], grant=grant())})
        body = self.get(page_size=1).json()
        self.assertIsNone(body["next_cursor"])
        self.assertEqual(len(body["items"]), 1)

    def test_no_authorized_company_returns_an_empty_list(self):
        self.environment({A: FakeCompanyDb(grant=None), B: FakeCompanyDb(grant=None), C: FakeCompanyDb(grant=None)})
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["total"], 0)
        self.assertFalse(response.json()["partial"])


class IsolationAndScopeTests(ConsolidatedBase):
    def test_company_without_grant_is_never_queried_even_if_requested(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb([ticket(B, 2, 2)], grant=grant()), C: FakeCompanyDb([ticket(C, 3, 3)], grant=None)}
        self.environment(dbs)
        body = self.get(tenant_ids=f"{A},{C}").json()
        self.assertEqual({item["tenant_id"] for item in body["items"]}, {A})
        self.assertEqual(dbs[C].incident_queries(), [])
        self.assertEqual(dbs[B].calls, [])  # not requested

    def test_without_tenant_ids_every_authorized_company_is_included_and_x_tenant_id_is_ignored(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb([ticket(B, 2, 2)], grant=grant()), C: FakeCompanyDb([ticket(C, 3, 3)], grant=None)}
        self.environment(dbs)
        response = self.client.get(URL, headers={"X-Test-User": "user-1", "X-Tenant-ID": C})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["tenant_id"] for item in response.json()["items"]}, {A, B})
        self.assertEqual(dbs[C].incident_queries(), [])

    def test_each_company_uses_its_own_row_scope(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant("viewer", "own")), B: FakeCompanyDb([ticket(B, 2, 2)], grant=grant("operator", "company")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        self.assertEqual(self.get().status_code, 200)
        own_sql, own_params = dbs[A].incident_queries()[0]
        company_sql, _ = dbs[B].incident_queries()[0]
        self.assertIn("requester_user_id=%s OR", own_sql)
        self.assertIn("user-1", own_params)
        self.assertNotIn("requester_user_id=%s OR", company_sql)
        for db, tenant in ((dbs[A], A), (dbs[B], B)):
            self.assertTrue(all("i.tenant_id=%s" in sql and tenant in params for sql, params in db.incident_queries()))

    def test_role_outside_the_ticket_roles_is_skipped(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant("superuser")), B: FakeCompanyDb([ticket(B, 2, 2)], grant=grant()), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        self.assertEqual({item["tenant_id"] for item in self.get().json()["items"]}, {B})

    def test_master_reads_all_active_companies_without_grants(self):
        dbs = {key: FakeCompanyDb([ticket(key, index, index)], grant=None) for index, key in enumerate((A, B, C), 1)}
        self.environment(dbs, master=True)
        self.assertEqual({item["tenant_id"] for item in self.get().json()["items"]}, {A, B, C})

    def test_inactive_user_is_rejected(self):
        self.environment({A: FakeCompanyDb(grant=grant())}, user={"id": "user-1", "active": False, "is_master": False})
        self.assertEqual(self.get().status_code, 403)

    def test_more_than_the_limit_of_authorized_companies_requires_tenant_ids(self):
        total = module.MAX_TENANTS + 1
        names = {f"{index:08d}-0000-4000-8000-000000000000": f"Empresa {index:02d}" for index in range(total)}
        dbs = {key: FakeCompanyDb(grant=grant()) for key in names}
        self.environment(dbs, names=names)
        self.assertEqual(self.get().status_code, 422)
        self.assertEqual(self.get(tenant_ids=",".join(list(names)[:3])).status_code, 200)


class PartialFailureTests(ConsolidatedBase):
    def test_one_company_failing_returns_partial_without_leaking_the_error(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb(grant=grant(), fail=RuntimeError("secret-db-detail")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        response = self.get()
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["partial"])
        self.assertEqual([item["tenant_id"] for item in body["items"]], [A])
        self.assertEqual(body["tenants_failed"], [{"tenant_id": B, "tenant_name": "Morada", "code": "error", "retryable": False}])
        self.assertNotIn("secret-db-detail", response.text)

    def test_timeout_is_reported_as_retryable(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb(grant=grant(), fail=QueryCanceled("slow")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        failed = self.get().json()["tenants_failed"][0]
        self.assertEqual((failed["code"], failed["retryable"]), ("timeout", True))

    def test_every_company_failing_is_a_503(self):
        dbs = {A: FakeCompanyDb(grant=grant(), fail=RuntimeError("x")), B: FakeCompanyDb(grant=grant(), fail=RuntimeError("y")), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        self.assertEqual(self.get().status_code, 503)

    def test_each_company_query_is_time_bounded(self):
        dbs = {A: FakeCompanyDb([ticket(A, 1, 1)], grant=grant()), B: FakeCompanyDb(grant=None), C: FakeCompanyDb(grant=None)}
        self.environment(dbs)
        self.get()
        self.assertIn(("SELECT set_config('statement_timeout', %s, true)", (str(module.TENANT_TIMEOUT_MS),)), dbs[A].calls)


class ValidationTests(ConsolidatedBase):
    def setUp(self):
        super().setUp()
        self.environment({A: FakeCompanyDb([ticket(A, 1, 1), ticket(A, 2, 2)], grant=grant()), B: FakeCompanyDb(grant=grant()), C: FakeCompanyDb(grant=None)})

    def test_page_size_above_the_limit_is_rejected(self):
        self.assertEqual(self.get(page_size=101).status_code, 422)
        self.assertEqual(self.get(page_size=0).status_code, 422)

    def test_malformed_and_too_many_tenant_ids_are_rejected(self):
        self.assertEqual(self.get(tenant_ids="not-a-uuid").status_code, 422)
        too_many = ",".join(f"{index:08d}-0000-4000-8000-000000000000" for index in range(module.MAX_TENANTS + 1))
        self.assertEqual(self.get(tenant_ids=too_many).status_code, 422)

    def test_invalid_cursor_is_rejected(self):
        self.assertEqual(self.get(cursor="@@@").status_code, 422)
        self.assertEqual(self.get(cursor="e30").status_code, 422)  # valid base64 of {} without the required keys

    def test_cursor_from_other_filters_is_rejected(self):
        cursor = self.get(page_size=1, q="a").json()["next_cursor"]
        self.assertIsNotNone(cursor)
        self.assertEqual(self.get(page_size=1, q="b", cursor=cursor).status_code, 422)
        self.assertEqual(self.get(page_size=1, q="a", cursor=cursor).status_code, 200)

    def test_cursor_from_other_companies_is_rejected(self):
        cursor = self.get(page_size=1).json()["next_cursor"]
        self.assertEqual(self.get(page_size=1, tenant_ids=A, cursor=cursor).status_code, 422)


class SummaryTests(ConsolidatedBase):
    def test_totals_are_summed_per_company_and_partial_is_reported(self):
        counters = lambda total, open_, critical: {"total": total, "open": open_, "critical": critical, "overdue": 1, "unassigned": 2, "pending_sync": 0}
        dbs = {A: FakeCompanyDb(grant=grant(), summary=counters(10, 7, 2)), B: FakeCompanyDb(grant=grant(), summary=counters(5, 3, 1)), C: FakeCompanyDb(grant=grant(), fail=RuntimeError("down"))}
        self.environment(dbs)
        body = self.get(SUMMARY_URL).json()
        self.assertEqual(body["totals"], {"total": 15, "open": 10, "critical": 3, "overdue": 2, "unassigned": 4, "pending_sync": 0})
        self.assertEqual({row["tenant_name"] for row in body["by_tenant"]}, {"Chaveou", "Morada"})
        self.assertTrue(body["partial"])
        self.assertEqual(body["tenants_failed"][0]["tenant_id"], C)
        self.assertTrue(body["read_only"])

    def test_summary_only_counts_authorized_companies(self):
        counters = {"total": 4, "open": 4, "critical": 0, "overdue": 0, "unassigned": 0, "pending_sync": 0}
        dbs = {A: FakeCompanyDb(grant=grant(), summary=counters), B: FakeCompanyDb(grant=None, summary=counters), C: FakeCompanyDb(grant=None, summary=counters)}
        self.environment(dbs)
        body = self.get(SUMMARY_URL).json()
        self.assertEqual(body["totals"]["total"], 4)
        self.assertEqual(dbs[B].incident_queries(), [])


class RoutingAndReadOnlyTests(unittest.TestCase):
    """Uses the OpenAPI document: FastAPI nests included routers, so app.routes is not flat."""

    def test_there_is_no_write_route_under_the_consolidated_prefix(self):
        paths = {path: operations for path, operations in app.openapi()["paths"].items() if path.startswith(URL)}
        self.assertEqual(set(paths), {URL, SUMMARY_URL})
        for path, operations in paths.items():
            self.assertEqual(set(operations), {"get"}, path)

    def test_consolidated_route_is_registered_before_the_ticket_id_route(self):
        paths = list(app.openapi()["paths"])
        self.assertLess(paths.index(URL), paths.index("/api/v1/tickets/{ticket_id}"))
        self.assertLess(paths.index(SUMMARY_URL), paths.index("/api/v1/tickets/{ticket_id}"))

    def test_cursor_round_trip(self):
        digest = module._filters_digest({"q": None}, [A])
        cursor = module.encode_cursor(BASE, "00000000-0000-4000-8000-000000000001", digest)
        self.assertEqual(module.decode_cursor(cursor, digest), (BASE, "00000000-0000-4000-8000-000000000001"))
        with self.assertRaises(HTTPException):
            module.decode_cursor(cursor, "other")


if __name__ == "__main__":
    unittest.main()
