import unittest

from central_monitor.command_bus import canonical_payload_hash


class CommandBusTests(unittest.TestCase):
    def test_hash_is_canonical(self):
        self.assertEqual(canonical_payload_hash({"b":2,"a":1}),canonical_payload_hash({"a":1,"b":2}))

    def test_different_payload_conflicts(self):
        self.assertNotEqual(canonical_payload_hash({"id":1}),canonical_payload_hash({"id":2}))


if __name__=="__main__":unittest.main()
