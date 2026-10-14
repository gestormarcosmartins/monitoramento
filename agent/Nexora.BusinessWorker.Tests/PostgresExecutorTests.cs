using Npgsql;
using Nexora.BusinessWorker;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

/// <summary>
/// Executor behaviour against a real PostgreSQL. Opt-in: set NEXORA_TEST_PG to a loopback connection string
/// (for example "Host=127.0.0.1;Port=5432;Database=erp_demo;Username=postgres"). Tables are created and dropped by the tests.
/// </summary>
public class PostgresExecutorTests : IDisposable
{
    private static readonly string? Connection = Environment.GetEnvironmentVariable("NEXORA_TEST_PG");
    private readonly DbQueryExecutor _executor = new(Vectors.Options(o => o.AllowPlaintextLoopbackDatabase = true));

    public PostgresExecutorTests()
    {
        if (Connection is null) return;
        Run("DROP TABLE IF EXISTS nx_t; CREATE TABLE nx_t(id int, nome text, valor numeric(12,2), quando timestamptz); " +
            "INSERT INTO nx_t SELECT g, CASE WHEN g % 2 = 0 THEN NULL ELSE 'n' || g END, g * 1.10, '2026-10-14 10:00:00+00' FROM generate_series(1, 8) g");
    }

    public void Dispose()
    {
        if (Connection is not null) Run("DROP TABLE IF EXISTS nx_t");
    }

    private static void Run(string sql)
    {
        using var c = new NpgsqlConnection(Connection);
        c.Open();
        using var command = new NpgsqlCommand(sql, c);
        command.ExecuteNonQuery();
    }

    private static Job MakeJob(string query, string columns, int maxRows = 500, int timeout = 15, int maxBytes = 1_000_000,
        Dictionary<string, System.Text.Json.JsonElement>? parameters = null)
    {
        var schema = columns.Split(',').Select(x => x.Split(':')).Select(p => new ColumnSpec { Name = p[0], Type = p[1] }).ToList();
        return new Job
        {
            ExecutionId = Guid.NewGuid(), Dialect = "postgres", DatabaseRef = "x", Query = query, ColumnSchema = schema,
            Constraints = new JobConstraints { MaxRows = maxRows, TimeoutSeconds = timeout, MaxBytes = maxBytes },
            Parameters = parameters ?? new(),
        };
    }

    [SkippableFact]
    public async Task Returns_columns_rows_nulls_and_exact_decimals()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var result = await _executor.ExecuteAsync(MakeJob("SELECT id, nome, valor, quando FROM nx_t ORDER BY id LIMIT 2", "id:integer,nome:string,valor:decimal,quando:datetime"), Connection!, default);
        Assert.Equal(2, result.RowCount);
        Assert.Equal("n1", result.Rows[0]["nome"]!.GetValue<string>());
        Assert.Null(result.Rows[1]["nome"]);                                     // SQL NULL stays null
        Assert.Equal("2.20", result.Rows[1]["valor"]!.GetValue<string>());
        Assert.Equal("2026-10-14T10:00:00.000000+00:00", result.Rows[0]["quando"]!.GetValue<string>());
    }

    [SkippableFact]
    public async Task Zero_rows_is_a_success()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var result = await _executor.ExecuteAsync(MakeJob("SELECT id FROM nx_t WHERE id < 0", "id:integer"), Connection!, default);
        Assert.Empty(result.Rows);
        Assert.False(result.Truncated);
    }

    [SkippableFact]
    public async Task The_row_cap_truncates_and_reports_it()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var result = await _executor.ExecuteAsync(MakeJob("SELECT id FROM nx_t ORDER BY id", "id:integer", maxRows: 3), Connection!, default);
        Assert.Equal(3, result.RowCount);
        Assert.True(result.Truncated);
    }

    [SkippableFact]
    public async Task A_slow_query_is_cancelled_at_the_timeout()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var started = DateTime.UtcNow;
        await Assert.ThrowsAsync<TimeoutException>(() =>
            _executor.ExecuteAsync(MakeJob("SELECT pg_sleep(20)::text AS x", "x:string", timeout: 1), Connection!, default));
        Assert.True((DateTime.UtcNow - started).TotalSeconds < 8, "must stop near the limit, not wait for the query");
    }

    [SkippableFact]
    public async Task The_session_is_read_only_even_if_a_write_reached_the_database()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var ex = await Assert.ThrowsAnyAsync<PostgresException>(() =>
            _executor.ExecuteAsync(MakeJob("INSERT INTO nx_t(id) VALUES (999) RETURNING id", "id:integer"), Connection!, default));
        Assert.Equal("25006", ex.SqlState);   // read_only_sql_transaction
    }

    [SkippableFact]
    public async Task Parameters_are_bound_not_concatenated()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var hostile = System.Text.Json.JsonDocument.Parse("\"1; DROP TABLE nx_t; --\"").RootElement;
        var job = MakeJob("SELECT id FROM nx_t WHERE nome = @nome", "id:integer", parameters: new() { ["nome"] = hostile });
        var result = await _executor.ExecuteAsync(job, Connection!, default);
        Assert.Empty(result.Rows);                                              // matched nothing, executed nothing
        var again = await _executor.ExecuteAsync(MakeJob("SELECT count(*) AS n FROM nx_t", "n:integer"), Connection!, default);
        Assert.Equal(8, again.Rows[0]["n"]!.GetValue<long>());
    }

    [SkippableFact]
    public async Task A_query_returning_other_columns_fails_the_contract()
    {
        Skip.If(Connection is null, "NEXORA_TEST_PG not set");
        var ex = await Assert.ThrowsAsync<ResultContractException>(() =>
            _executor.ExecuteAsync(MakeJob("SELECT id, nome FROM nx_t", "id:integer"), Connection!, default));
        Assert.Equal("schema_mismatch", ex.Code);
    }

    [Fact]
    public void Transport_security_is_enforced_unless_explicitly_relaxed()
    {
        var strict = new DbQueryExecutor(Vectors.Options());
        Assert.Contains("VerifyFull", strict.Harden("postgres", "Host=db.example;Username=u;Password=p;Database=d", 15), StringComparison.OrdinalIgnoreCase);
        var sql = strict.Harden("sqlserver", "Server=db.example;User Id=u;Password=p;TrustServerCertificate=True", 15);
        Assert.Contains("Trust Server Certificate=False", sql, StringComparison.OrdinalIgnoreCase);   // cannot be switched on by the string alone
        Assert.Contains("ReadOnly", sql, StringComparison.OrdinalIgnoreCase);
        var relaxed = new DbQueryExecutor(Vectors.Options(o => o.AllowPlaintextLoopbackDatabase = true));
        Assert.DoesNotContain("VerifyFull", relaxed.Harden("postgres", "Host=127.0.0.1;Username=u;Database=d;SSL Mode=Disable", 15), StringComparison.OrdinalIgnoreCase);
        Assert.Contains("VerifyFull", relaxed.Harden("postgres", "Host=db.example;Username=u;Database=d", 15), StringComparison.OrdinalIgnoreCase);  // loopback only
    }
}
