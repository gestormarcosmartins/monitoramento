import unittest
from uuid import uuid4

from pydantic import ValidationError

from central_monitor import business_monitors as bm


class SourceUpdateTests(unittest.TestCase):
    def test_dialect_cannot_be_changed_through_the_update_contract(self):
        self.assertNotIn("dialect", bm.SourceUpdate.model_fields)

    def test_logical_name_must_keep_the_safe_pattern(self):
        worker = uuid4()
        for bad in ("Erp", "1erp", "erp-jri", "erp jri", "a"):
            with self.assertRaises(ValidationError):
                bm.SourceUpdate(name="JRI", worker_id=worker, database_ref=bad)
        bm.SourceUpdate(name="JRI", worker_id=worker, database_ref="erp_jri")

    def test_only_moving_worker_or_database_cancels_queued_work(self):
        w1, w2 = uuid4(), uuid4()
        current = {"worker_id": w1, "database_ref": "erp_jri"}
        rename = bm.SourceUpdate(name="Novo nome", worker_id=w1, database_ref="erp_jri")
        move = bm.SourceUpdate(name="xx", worker_id=w2, database_ref="erp_jri")
        redirect = bm.SourceUpdate(name="xx", worker_id=w1, database_ref="outro_banco")
        self.assertFalse(bm.source_change_cancels_work(current, rename))
        self.assertTrue(bm.source_change_cancels_work(current, move))
        self.assertTrue(bm.source_change_cancels_work(current, redirect))

    def test_worker_capacity_is_bounded(self):
        with self.assertRaises(ValidationError):
            bm.WorkerUpdate(name="worker", capacity=5)
        with self.assertRaises(ValidationError):
            bm.WorkerUpdate(name="worker", capacity=0)
        self.assertEqual(bm.WorkerUpdate(name="worker", capacity=4).capacity, 4)

    def test_new_routes_are_registered_on_the_master_router(self):
        paths = {(tuple(sorted(r.methods)), r.path) for r in bm.master_router.routes}
        self.assertIn((("PUT",), "/api/v1/master/business/workers/{worker_id}"), paths)
        self.assertIn((("PUT",), "/api/v1/master/business/data-sources/{source_id}"), paths)
        self.assertIn((("POST",), "/api/v1/master/business/data-sources/{source_id}/active"), paths)
        self.assertIn((("GET",), "/api/v1/master/business/install-info"), paths)


if __name__ == "__main__":
    unittest.main()
