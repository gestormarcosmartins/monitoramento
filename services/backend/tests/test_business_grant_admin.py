import unittest

try:
    from fastapi import HTTPException
    from central_monitor import business
except ImportError:  # backend dependencies are not installed
    business = None


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDb:
    """Answers the two queries made by _require_grant_admin."""

    def __init__(self, enabled=True, grant_scope=None):
        self.enabled, self.grant_scope = enabled, grant_scope

    def execute(self, sql, params=()):
        if "cm_feature_flags" in sql:
            return FakeResult({"mode": "enabled"} if self.enabled else None)
        return FakeResult({"scope_type": self.grant_scope} if self.grant_scope else None)


@unittest.skipIf(business is None, "backend dependencies not installed")
class GrantAdminTests(unittest.TestCase):
    def check(self, db, actor, capability=None):
        business._require_grant_admin(db, "t", actor, capability)

    def test_disabled_product_blocks_even_master(self):
        with self.assertRaises(HTTPException) as ctx:
            self.check(FakeDb(enabled=False), {"id": 1, "is_master": True})
        self.assertEqual(ctx.exception.status_code, 404)

    def test_company_scope_manager_allowed(self):
        self.check(FakeDb(grant_scope="company"), {"id": 1}, "business.view")

    def test_scoped_manager_cannot_grant(self):
        for scope in ("own", "unit", "department", "process"):
            with self.assertRaises(HTTPException) as ctx:
                self.check(FakeDb(grant_scope=scope), {"id": 1}, "business.view")
            self.assertEqual(ctx.exception.status_code, 403)

    def test_master_can_only_bootstrap_administrator(self):
        master = {"id": 1, "is_master": True}
        self.check(FakeDb(), master, "business.grants.manage")
        with self.assertRaises(HTTPException):
            self.check(FakeDb(), master, "business.view")


if __name__ == "__main__":
    unittest.main()
