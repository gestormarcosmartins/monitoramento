import unittest

try:
    from central_monitor.business_sql import SqlRejected, validate_query
except ImportError:  # sqlglot / backend dependencies not installed
    validate_query = None


def ok(sql, dialect="sqlserver", params=None):
    return validate_query(sql, dialect, params or {})


@unittest.skipIf(validate_query is None, "backend dependencies not installed")
class ValidSelects(unittest.TestCase):
    def test_plain_selects_pass_in_both_dialects(self):
        self.assertTrue(ok("SELECT TOP 5 documento, valor FROM dbo.vw_nexora_cr WHERE vencido = 1").sha256)
        self.assertTrue(ok("SELECT a, SUM(b) AS t FROM x GROUP BY a HAVING SUM(b) > 0", "postgres").sha256)
        self.assertTrue(ok("SELECT a FROM x WITH (NOLOCK)").sha256)
        self.assertTrue(ok("SELECT a FROM x UNION ALL SELECT b FROM y").sha256)
        self.assertTrue(ok("WITH c AS (SELECT 1 AS a) SELECT a FROM c", "postgres").sha256)
        self.assertTrue(ok("SELECT a FROM x;").sha256)  # trailing semicolon is still one statement

    def test_parameters_are_bound_and_round_trip(self):
        for dialect in ("sqlserver", "postgres"):
            result = ok("SELECT a FROM x WHERE d < @hoje AND s = @status", dialect, {"hoje": "2026-10-14", "status": "A"})
            self.assertEqual(result.parameters, ("hoje", "status"))
            self.assertIn("@hoje", result.normalized_sql)
            self.assertNotIn("__p_", result.normalized_sql)

    def test_hash_is_stable_and_formatting_independent(self):
        a = ok("SELECT   a FROM   x")
        b = ok("select a\nfrom x")
        self.assertEqual(a.sha256, b.sha256)


@unittest.skipIf(validate_query is None, "backend dependencies not installed")
class Rejections(unittest.TestCase):
    def reject(self, sql, dialect="sqlserver", params=None):
        with self.assertRaises(SqlRejected, msg=sql):
            validate_query(sql, dialect, params or {})

    def test_dml_ddl_and_multiple_statements(self):
        for sql in ("UPDATE x SET a=1", "DELETE FROM x", "INSERT INTO x(a) VALUES (1)", "DROP TABLE x",
                    "ALTER TABLE x ADD b int", "TRUNCATE TABLE x", "MERGE INTO x USING y ON 1=1 WHEN MATCHED THEN DELETE",
                    "CREATE TABLE t(a int)", "SELECT 1; DROP TABLE x", "SELECT 1; SELECT 2"):
            self.reject(sql)
        self.reject("COPY x TO '/tmp/f'", "postgres")
        self.reject("CREATE TABLE t(a int)", "postgres")

    def test_select_into_and_locking(self):
        self.reject("SELECT a INTO #t FROM x")
        self.reject("SELECT a INTO newt FROM x", "postgres")
        self.reject("SELECT a FROM x FOR UPDATE", "postgres")

    def test_dynamic_execution_and_procedures(self):
        for sql in ("EXEC sp_who", "EXEC('select 1')", "DECLARE @a int = 1; SELECT @a", "BULK INSERT x FROM 'f'"):
            self.reject(sql)

    def test_dml_hidden_in_cte(self):
        self.reject("WITH c AS (SELECT 1 a) DELETE FROM x")
        self.reject("WITH d AS (DELETE FROM x RETURNING *) SELECT * FROM d", "postgres")

    def test_external_access_and_side_effect_functions(self):
        for sql in ("SELECT * FROM OPENROWSET('SQLNCLI','a','select 1')", "SELECT * FROM OPENQUERY(srv,'select 1')",
                    "SELECT xp_cmdshell('dir')", "SELECT sp_something(1)"):
            self.reject(sql)
        for sql in ("SELECT pg_sleep(10)", "SELECT lo_import('/etc/passwd')", "SELECT set_config('a','b',false)",
                    "SELECT nextval('s')", "SELECT * FROM dblink('c','select 1') AS t(a int)",
                    "SELECT pg_read_file('/etc/passwd')", "SELECT pg_terminate_backend(1)"):
            self.reject(sql, "postgres")

    def test_linked_servers_and_temp_tables(self):
        self.reject("SELECT a FROM srv.db.dbo.t")
        self.reject("SELECT a FROM #temp")

    def test_system_variables_and_parameter_mismatch(self):
        self.reject("SELECT @@version")
        self.reject("SELECT a FROM x WHERE d < @hoje")  # undeclared
        self.reject("SELECT a FROM x", params={"hoje": "x"})  # declared but unused
        self.reject("SELECT a FROM x WHERE d < @bad-name", params={"bad-name": "x"})

    def test_limits_and_garbage(self):
        self.reject("")
        self.reject("   ")
        self.reject("SELECT " + "a," * 20000 + "b FROM x")
        self.reject("SELECT FROM WHERE")
        self.reject("SELECT 1", dialect="oracle")
        self.reject("SELECT 1\x00")


if __name__ == "__main__":
    unittest.main()
