import base64
import os
import unittest
from decimal import Decimal
from unittest import mock

try:
    from central_monitor import business_results as br
except ImportError:  # backend dependencies are not installed
    br = None

SCHEMA = [{"name": "documento", "type": "string"}, {"name": "cliente", "type": "string"},
          {"name": "cpf", "type": "string"}, {"name": "celular", "type": "string"},
          {"name": "valor_vencido", "type": "decimal"}]
LIMITS = {"timeout_seconds": 15, "max_rows": 3, "max_bytes": 1000}


def payload(rows, **over):
    base = {"columns": SCHEMA, "rows": rows, "row_count": len(rows), "truncated": False}
    return {**base, **over}


ROW = {"documento": "CR-001", "cliente": "Exemplo", "cpf": None, "celular": None, "valor_vencido": 1200.50}


@unittest.skipIf(br is None, "backend dependencies not installed")
class ResultContract(unittest.TestCase):
    def test_zero_one_and_many_rows(self):
        self.assertEqual(br.validate_result(SCHEMA, LIMITS, payload([])).row_count, 0)  # empty is a success
        one = br.validate_result(SCHEMA, LIMITS, payload([ROW]))
        self.assertEqual(one.rows[0]["cpf"], None)  # null preserved, not "" or 0
        self.assertEqual(one.rows[0]["valor_vencido"], "1200.5")  # exact decimal, kept as text
        self.assertEqual(br.validate_result(SCHEMA, LIMITS, payload([ROW, ROW, ROW])).row_count, 3)

    def test_five_columns_are_five_properties_of_one_object(self):
        result = br.validate_result(SCHEMA, LIMITS, payload([ROW]))
        self.assertEqual(list(result.rows[0]), [c["name"] for c in SCHEMA])

    def test_schema_mismatch_missing_or_extra_keys_rejected(self):
        for bad in (payload([ROW], columns=SCHEMA[:-1]),
                    payload([{k: v for k, v in ROW.items() if k != "cpf"}]),
                    payload([{**ROW, "extra": 1}]), payload([ROW], row_count=2), payload("nope")):
            with self.assertRaises(br.ContractError):
                br.validate_result(SCHEMA, LIMITS, bad)

    def test_type_checks(self):
        for cell, bad in (("valor_vencido", "abc"), ("valor_vencido", True), ("documento", 5)):
            with self.assertRaises(br.ContractError):
                br.validate_result(SCHEMA, LIMITS, payload([{**ROW, cell: bad}]))
        ints = [{"name": "n", "type": "integer"}]
        with self.assertRaises(br.ContractError):
            br.validate_result(ints, LIMITS, {"columns": ints, "rows": [{"n": True}], "row_count": 1, "truncated": False})
        stamp = [{"name": "t", "type": "datetime"}]
        ok = br.validate_result(stamp, LIMITS, {"columns": stamp, "rows": [{"t": "2026-10-14T10:00:00Z"}], "row_count": 1, "truncated": False})
        self.assertTrue(ok.rows[0]["t"].endswith("+00:00"))
        with self.assertRaises(br.ContractError):  # no timezone: refused
            br.validate_result(stamp, LIMITS, {"columns": stamp, "rows": [{"t": "2026-10-14T10:00:00"}], "row_count": 1, "truncated": False})

    def test_limits_and_truncation_flag(self):
        with self.assertRaises(br.ContractError):
            br.validate_result(SCHEMA, LIMITS, payload([ROW] * 4))
        with self.assertRaises(br.ContractError):  # truncated claimed but the cap was not reached
            br.validate_result(SCHEMA, LIMITS, payload([ROW], truncated=True))
        self.assertTrue(br.validate_result(SCHEMA, LIMITS, payload([ROW] * 3, truncated=True)).truncated)
        with self.assertRaises(br.ContractError):
            br.validate_result(SCHEMA, {**LIMITS, "max_bytes": 50}, payload([ROW]))

    def test_schema_and_limit_validation(self):
        self.assertEqual(br.validate_columns(SCHEMA), SCHEMA)
        for bad in ([], [{"name": "a", "type": "money"}], [{"name": "a", "type": "string"}, {"name": "A", "type": "string"}],
                    [{"name": "1a", "type": "string"}], [{"name": "a", "type": "string", "x": 1}]):
            with self.assertRaises(br.ContractError):
                br.validate_columns(bad)
        self.assertEqual(br.validate_limits(None)["max_rows"], 500)
        for bad in ({"max_rows": 5001}, {"timeout_seconds": 0}, {"max_rows": True}, {"surprise": 1}):
            with self.assertRaises(br.ContractError):
                br.validate_limits(bad)


@unittest.skipIf(br is None, "backend dependencies not installed")
class Metrics(unittest.TestCase):
    cols = [{"name": "v", "type": "decimal"}, {"name": "s", "type": "string"}]

    def test_validate_metric(self):
        self.assertEqual(br.validate_metric({"kind": "row_count"}, self.cols), {"kind": "row_count"})
        self.assertEqual(br.validate_metric({"kind": "sum", "column": "v"}, self.cols)["column"], "v")
        for bad in ({"kind": "sum", "column": "s"}, {"kind": "sum", "column": "zz"}, {"kind": "sum"},
                    {"kind": "row_count", "column": "v"}, {"kind": "avg", "column": "v"}):
            with self.assertRaises(br.ContractError):
                br.validate_metric(bad, self.cols)
        self.assertEqual(br.validate_metric({"kind": "count_nonnull", "column": "s"}, self.cols)["kind"], "count_nonnull")

    def test_compute(self):
        rows = [{"v": "10.10", "s": "a"}, {"v": None, "s": None}, {"v": "5", "s": "b"}]
        self.assertEqual(br.compute_metric({"kind": "row_count"}, rows), Decimal(3))
        self.assertEqual(br.compute_metric({"kind": "sum", "column": "v"}, rows), Decimal("15.10"))  # exact, no float drift
        self.assertEqual(br.compute_metric({"kind": "max", "column": "v"}, rows), Decimal("10.10"))
        self.assertEqual(br.compute_metric({"kind": "count_nonnull", "column": "s"}, rows), Decimal(2))

    def test_empty_and_all_null_are_not_confused(self):
        self.assertEqual(br.compute_metric({"kind": "row_count"}, []), Decimal(0))
        self.assertEqual(br.compute_metric({"kind": "sum", "column": "v"}, []), Decimal(0))  # nothing overdue
        self.assertIsNone(br.compute_metric({"kind": "sum", "column": "v"}, [{"v": None, "s": "x"}]))  # unknown, not zero
        self.assertIsNone(br.compute_metric({"kind": "first_value", "column": "v"}, []))
        self.assertIsNone(br.compute_metric({"kind": "max", "column": "v"}, []))


@unittest.skipIf(br is None, "backend dependencies not installed")
class Signing(unittest.TestCase):
    def setUp(self):
        self.seed = base64.b64encode(os.urandom(32)).decode()
        patcher = mock.patch.object(br, "get_settings", return_value=mock.Mock(business_signing_key=self.seed))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sign_and_verify(self):
        manifest = {"monitor_id": "m1", "revision": 3, "query": "SELECT 1", "constraints": {"timeout_seconds": 15}}
        signature = br.sign_manifest(manifest)
        key = br.public_key_b64()
        self.assertTrue(br.verify_manifest(manifest, signature, key))
        self.assertTrue(br.verify_manifest(dict(reversed(list(manifest.items()))), signature, key))  # key order irrelevant
        self.assertFalse(br.verify_manifest({**manifest, "query": "SELECT 2"}, signature, key))  # tampered query
        self.assertFalse(br.verify_manifest({**manifest, "revision": 4}, signature, key))
        self.assertFalse(br.verify_manifest(manifest, "AAAA", key))

    def test_missing_or_bad_key_fails_closed(self):
        with mock.patch.object(br, "get_settings", return_value=mock.Mock(business_signing_key="")):
            with self.assertRaises(RuntimeError):
                br.sign_manifest({"a": 1})
        with mock.patch.object(br, "get_settings", return_value=mock.Mock(business_signing_key=base64.b64encode(b"short").decode())):
            with self.assertRaises(RuntimeError):
                br.sign_manifest({"a": 1})


if __name__ == "__main__":
    unittest.main()


class MetricColumnWhitespaceTests(unittest.TestCase):
    def test_metric_column_ignores_surrounding_whitespace(self):
        from central_monitor.business_results import validate_metric
        columns = [{"name": "total_vencido_90", "type": "decimal"}]
        self.assertEqual(validate_metric({"kind": "first_value", "column": " total_vencido_90 "}, columns),
                         {"kind": "first_value", "column": "total_vencido_90"})

    def test_unknown_metric_column_error_lists_the_declared_columns(self):
        from central_monitor.business_results import ContractError, validate_metric
        columns = [{"name": "total_vencido_90", "type": "decimal"}]
        with self.assertRaises(ContractError) as caught:
            validate_metric({"kind": "sum", "column": "outra"}, columns)
        self.assertIn("total_vencido_90", str(caught.exception))
