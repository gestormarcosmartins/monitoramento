import unittest
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError

from central_monitor import business_monitors as bm


class WorkerCredentialTests(unittest.TestCase):
    def test_worker_token_pair_is_accepted(self):
        self.assertEqual(bm.worker_credentials("w", "t", None, None), ("worker", "w", "t"))

    def test_agent_identity_is_accepted(self):
        self.assertEqual(bm.worker_credentials(None, None, "a", "t"), ("agent", "a", "t"))

    def test_mixed_missing_or_partial_credentials_are_refused(self):
        for args in [(None, None, None, None), ("w", None, None, None), (None, "t", None, None), (None, None, "a", None),
                     (None, None, None, "t"), ("w", "t", "a", "t2"), ("w", "t", "a", None), ("w", None, None, "t")]:
            with self.assertRaises(HTTPException) as caught:
                bm.worker_credentials(*args)
            self.assertEqual(caught.exception.status_code, 401, args)


class AgentWorkerContractTests(unittest.TestCase):
    def test_from_agent_payload(self):
        agent = uuid4()
        self.assertEqual(bm.WorkerFromAgent(agent_id=agent).capacity, 1)
        with self.assertRaises(ValidationError):
            bm.WorkerFromAgent(agent_id=agent, capacity=9)
        with self.assertRaises(ValidationError):
            bm.WorkerFromAgent(agent_id="not-a-uuid")

    def test_routes_exist(self):
        master = {(tuple(sorted(r.methods)), r.path) for r in bm.master_router.routes}
        worker = {(tuple(sorted(r.methods)), r.path) for r in bm.worker_router.routes}
        self.assertIn((("GET",), "/api/v1/master/business/agents"), master)
        self.assertIn((("POST",), "/api/v1/master/business/workers/from-agent"), master)
        self.assertIn((("GET",), "/api/v1/agents/business/identity"), worker)

    def test_every_worker_route_uses_the_dual_credential_dependency(self):
        for route in bm.worker_router.routes:
            names = [d.call.__name__ for d in route.dependant.dependencies]
            self.assertIn("worker_identity", names, route.path)


if __name__ == "__main__":
    unittest.main()
