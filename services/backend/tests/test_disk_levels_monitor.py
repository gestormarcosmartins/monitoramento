import unittest

from central_monitor import agent_api


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    """Responde aos SELECT por nome da tabela e registra os INSERT."""

    def __init__(self, monitor_exists=False, asset_id=None):
        self.monitor_exists, self.asset_id, self.inserts = monitor_exists, asset_id, []

    def execute(self, sql, params=()):
        text = " ".join(sql.split())
        if text.startswith("SELECT 1 FROM cm_monitors"):
            return FakeResult({"x": 1} if self.monitor_exists else None)
        if text.startswith("SELECT id FROM cm_assets"):
            return FakeResult({"id": self.asset_id} if self.asset_id else None)
        if text.startswith("INSERT INTO cm_assets"):
            return FakeResult({"id": "novo-ambiente"})
        if text.startswith("INSERT INTO cm_monitors"):
            self.inserts.append((text, params))
            return FakeResult(None)
        raise AssertionError(f"SQL inesperado: {text[:60]}")


class DiskWarningMonitorTests(unittest.TestCase):
    def test_creates_the_attention_monitor_for_an_existing_environment(self):
        conn = FakeConnection(asset_id="asset-1")
        agent_api._ensure_disk_warning_monitor(conn, "tenant-1", "JRI - SRVX", create_asset=False)
        self.assertEqual(len(conn.inserts), 1)
        text, params = conn.inserts[0]
        self.assertIn("ON CONFLICT(tenant_id,name) DO NOTHING", text)  # nunca reativa nem sobrescreve
        self.assertEqual(params[0:3], ("tenant-1", "asset-1", "JRI - SRVX - Espaço em disco (atenção)"))
        self.assertEqual(params[4], 300)
        self.assertEqual(params[5], "warning")
        self.assertEqual(params[3].obj, {"source": "windows-agent-direct", "code": "host-disk-warning:JRI - SRVX"})

    def test_does_nothing_when_the_monitor_already_exists_even_if_it_was_deactivated(self):
        conn = FakeConnection(monitor_exists=True, asset_id="asset-1")
        agent_api._ensure_disk_warning_monitor(conn, "tenant-1", "JRI - SRVX", create_asset=False)
        self.assertEqual(conn.inserts, [])

    def test_heartbeat_path_never_creates_or_reactivates_the_environment(self):
        conn = FakeConnection(asset_id=None)
        agent_api._ensure_disk_warning_monitor(conn, "tenant-1", "JRI - SRVX", create_asset=False)
        self.assertEqual(conn.inserts, [])

    def test_enrollment_path_creates_the_environment_when_needed(self):
        conn = FakeConnection(asset_id=None)
        agent_api._ensure_disk_warning_monitor(conn, "tenant-1", "JRI - SRVX")
        self.assertEqual(len(conn.inserts), 1)
        self.assertEqual(conn.inserts[0][1][1], "novo-ambiente")

    def test_the_new_monitor_is_not_part_of_the_default_host_set(self):
        # agentes antigos não declaram host-disk-levels: não podem ganhar um monitor eternamente "sem dados"
        self.assertNotIn("host-disk-warning", [code for code, *_ in agent_api._HOST_MONITORS])
        self.assertEqual(agent_api._DISK_WARNING_MONITOR[0], "host-disk-warning")
        self.assertEqual(agent_api._DISK_WARNING_MONITOR[2], "warning")


if __name__ == "__main__":
    unittest.main()
