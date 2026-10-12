import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from central_monitor import db


class DatabasePoolTests(unittest.TestCase):
    def tearDown(self) -> None:
        db.get_pool.cache_clear()

    @patch("central_monitor.db.ConnectionPool")
    @patch("central_monitor.db.get_settings")
    def test_pool_uses_configured_bounds_and_dict_rows(self, get_settings, connection_pool) -> None:
        get_settings.return_value = SimpleNamespace(
            central_database_url="postgresql://example",
            database_pool_min_size=3,
            database_pool_max_size=9,
            database_pool_timeout_seconds=4.5,
        )

        pool = db.get_pool()

        self.assertIs(pool, connection_pool.return_value)
        connection_pool.assert_called_once()
        options = connection_pool.call_args.kwargs
        self.assertEqual(options["min_size"], 3)
        self.assertEqual(options["max_size"], 9)
        self.assertEqual(options["timeout"], 4.5)
        self.assertEqual(options["name"], "central-monitor-api")
        self.assertIn("row_factory", options["kwargs"])

    @patch("central_monitor.db.ConnectionPool")
    @patch("central_monitor.db.get_settings")
    def test_pool_rejects_inverted_bounds(self, get_settings, _connection_pool) -> None:
        get_settings.return_value = SimpleNamespace(
            central_database_url="postgresql://example",
            database_pool_min_size=10,
            database_pool_max_size=2,
            database_pool_timeout_seconds=5,
        )

        with self.assertRaisesRegex(ValueError, "MIN_SIZE"):
            db.get_pool()

    @patch("central_monitor.db.get_pool")
    def test_connect_and_stats_use_shared_pool(self, get_pool) -> None:
        context = MagicMock()
        get_pool.return_value.connection.return_value = context
        get_pool.return_value.get_stats.return_value = {
            "pool_size": 4,
            "pool_available": 2,
            "requests_waiting": 1,
            "connections_ms": 999,
        }

        self.assertIs(db.connect(), context)
        self.assertEqual(
            db.pool_stats(),
            {"pool_size": 4, "pool_available": 2, "requests_waiting": 1},
        )


if __name__ == "__main__":
    unittest.main()
