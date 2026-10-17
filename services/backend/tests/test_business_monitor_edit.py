import unittest
from uuid import uuid4

from pydantic import ValidationError

from central_monitor import business_monitors as bm


def current(**over):
    base = {"name": "JRI - Vencido", "description": "", "indicator_id": uuid4(), "source_id": uuid4(), "interval_seconds": 300}
    base.update(over)
    return base


def payload_from(row, **over):
    data = {"name": row["name"], "description": row["description"], "indicator_id": row["indicator_id"],
            "source_id": row["source_id"], "interval_seconds": row["interval_seconds"]}
    data.update(over)
    return bm.MonitorEdit(**data)


class MonitorEditTests(unittest.TestCase):
    def test_nothing_changed_reports_no_changes(self):
        row = current()
        self.assertEqual(bm.monitor_changes(row, payload_from(row)), [])

    def test_each_edited_field_is_reported(self):
        row = current()
        self.assertEqual(bm.monitor_changes(row, payload_from(row, name="Outro nome")), ["name"])
        self.assertEqual(bm.monitor_changes(row, payload_from(row, description="nova")), ["description"])
        self.assertEqual(bm.monitor_changes(row, payload_from(row, interval_seconds=3600)), ["interval_seconds"])
        self.assertEqual(bm.monitor_changes(row, payload_from(row, source_id=uuid4())), ["source_id"])
        self.assertEqual(bm.monitor_changes(row, payload_from(row, indicator_id=uuid4())), ["indicator_id"])
        self.assertEqual(sorted(bm.monitor_changes(row, payload_from(row, name="x y z", interval_seconds=60))), ["interval_seconds", "name"])

    def test_a_null_description_equals_an_empty_one(self):
        row = current(description=None)
        self.assertEqual(bm.monitor_changes(row, payload_from(row, description="")), [])

    def test_interval_and_name_limits_are_enforced(self):
        row = current()
        for bad in (59, 86401, 0, -5):
            with self.assertRaises(ValidationError):
                payload_from(row, interval_seconds=bad)
        with self.assertRaises(ValidationError):
            payload_from(row, name="ab")
        payload_from(row, interval_seconds=60)
        payload_from(row, interval_seconds=86400)

    def test_the_sql_text_is_not_part_of_the_edit_contract(self):
        for field in ("query", "columns", "metric", "parameters", "limits"):
            self.assertNotIn(field, bm.MonitorEdit.model_fields)

    def test_moving_to_another_database_type_is_refused(self):
        self.assertTrue(bm.source_move_allowed("sqlserver", "sqlserver"))
        self.assertFalse(bm.source_move_allowed("sqlserver", "postgres"))

    def test_route_is_a_master_route(self):
        paths = {(tuple(sorted(r.methods)), r.path) for r in bm.master_router.routes}
        self.assertIn((("PUT",), "/api/v1/master/business/monitors/{monitor_id}"), paths)


if __name__ == "__main__":
    unittest.main()
