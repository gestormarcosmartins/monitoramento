using System.Data.Common;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using Nexora.BusinessWorker;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

public class SecretResolverTests
{
    [Fact]
    public void Connection_strings_come_from_the_customer_side_only()
    {
        var env = new Dictionary<string, string?> { ["NEXORA_BW_DB_ERP_PRINCIPAL"] = " Server=x;User Id=ro;Password=p " };
        var resolver = new SecretResolver(Vectors.Options(), k => env.GetValueOrDefault(k));
        Assert.Equal("Server=x;User Id=ro;Password=p", resolver.ConnectionString("erp_principal"));
        Assert.Null(resolver.ConnectionString("outro"));
    }

    [Theory]
    [InlineData("../secrets")]
    [InlineData("a/b")]
    [InlineData("ERP")]
    [InlineData("")]
    public void References_that_could_escape_the_secrets_directory_are_refused(string reference)
    {
        var directory = Directory.CreateTempSubdirectory().FullName;
        File.WriteAllText(Path.Combine(directory, "secret.conn"), "Server=leak");
        var resolver = new SecretResolver(Vectors.Options(o => o.SecretsDirectory = directory), _ => null);
        Assert.Null(resolver.ConnectionString(reference));
    }

    [Fact]
    public void Falls_back_to_a_file_in_the_secrets_directory_and_reads_the_token_from_a_file()
    {
        var directory = Directory.CreateTempSubdirectory().FullName;
        File.WriteAllText(Path.Combine(directory, "erp_principal.conn"), "Host=db\n");
        File.WriteAllText(Path.Combine(directory, "token"), "tok-123\n");
        var resolver = new SecretResolver(Vectors.Options(o => { o.SecretsDirectory = directory; o.TokenFile = Path.Combine(directory, "token"); }), _ => null);
        Assert.Equal("Host=db", resolver.ConnectionString("erp_principal"));
        Assert.Equal("tok-123", resolver.WorkerToken());
    }
}

public class ParameterBinderTests
{
    private static NpgsqlParameter Bind(string dialect, string text)
    {
        var command = new NpgsqlCommand();
        ParameterBinder.Add(command, dialect, "p", JsonDocument.Parse(JsonSerializer.Serialize(text)).RootElement);
        return (NpgsqlParameter)command.Parameters[0];
    }

    [Fact]
    public void Parameters_are_bound_with_an_at_sign_name_and_iso_dates_become_typed_values()
    {
        Assert.IsType<DateOnly>(Bind("postgres", "2026-10-14").Value);
        Assert.Equal(DateTimeKind.Utc, ((DateTime)Bind("postgres", "2026-10-14T10:00:00-03:00").Value!).Kind);
        Assert.Equal("texto", Bind("postgres", "texto").Value);
        Assert.Equal(System.Data.DbType.Date, Bind("sqlserver", "2026-10-14").DbType);
    }

    [Fact]
    public void Structured_parameter_values_are_refused()
    {
        var command = new NpgsqlCommand();
        var ex = Assert.Throws<JobRejectedException>(() =>
            ParameterBinder.Add(command, "postgres", "p", JsonDocument.Parse("{\"a\":1}").RootElement));
        Assert.Equal("unsupported_parameter", ex.Code);
    }
}

public class JobRunnerTests
{
    private sealed class FakeApi : ICentralApi
    {
        public bool LeaseValid = true;
        public int ResultFailuresBeforeSuccess;
        public List<(Guid Id, ResultBody Body)> Results = new();
        public List<(Guid Id, FailureBody Body)> Failures = new();
        public int Started;
        public Task HeartbeatAsync(CancellationToken ct) => Task.CompletedTask;
        public Task<IReadOnlyList<JobEnvelope>> ClaimAsync(int limit, CancellationToken ct) => Task.FromResult<IReadOnlyList<JobEnvelope>>(new List<JobEnvelope>());
        public Task<bool> StartedAsync(Guid id, CancellationToken ct) { Started++; return Task.FromResult(LeaseValid); }
        public Task<bool> RenewAsync(Guid id, CancellationToken ct) => Task.FromResult(true);
        public Task<string> ResultAsync(Guid id, ResultBody body, CancellationToken ct)
        {
            if (ResultFailuresBeforeSuccess-- > 0) throw new HttpRequestException("down");
            Results.Add((id, body));
            return Task.FromResult("accepted");
        }
        public Task FailedAsync(Guid id, FailureBody body, CancellationToken ct) { Failures.Add((id, body)); return Task.CompletedTask; }
        public Task<JobEnvelope?> CheckUpdateAsync(string platform, CancellationToken ct) => Task.FromResult<JobEnvelope?>(null);
        public Task DownloadReleaseAsync(string version, string platform, Stream destination, long maxBytes, CancellationToken ct) => Task.CompletedTask;
        public Task ReportUpdateAsync(string version, string status, string error, CancellationToken ct) => Task.CompletedTask;
    }

    private sealed class FakeExecutor : IQueryExecutor
    {
        public Func<Job, string, Task<ResultBody>> Behaviour = (_, _) => Task.FromResult(new ResultBody { RowCount = 0, DurationMs = 5 });
        public Job? Seen;
        public string? ConnectionSeen;
        public Task<ResultBody> ExecuteAsync(Job job, string connectionString, CancellationToken stop) { Seen = job; ConnectionSeen = connectionString; return Behaviour(job, connectionString); }
    }

    private static (JobRunner Runner, FakeApi Api, FakeExecutor Executor) Build(string? connection = "Host=db;Username=ro", TimeProvider? time = null)
    {
        var api = new FakeApi();
        var executor = new FakeExecutor();
        var options = Vectors.Options();
        var secrets = new SecretResolver(options, k => k == "NEXORA_BW_DB_ERP_PRINCIPAL" ? connection : null);
        var runner = new JobRunner(api, executor, secrets, options, NullLogger<JobRunner>.Instance, time ?? new FixedTime(Vectors.Now),
            new[] { TimeSpan.Zero, TimeSpan.Zero, TimeSpan.Zero });
        return (runner, api, executor);
    }

    private sealed class FixedTime(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    [Fact]
    public async Task A_verified_job_is_executed_with_the_local_connection_and_the_result_is_reported()
    {
        var (runner, api, executor) = Build();
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Equal(1, api.Started);
        Assert.Single(api.Results);
        Assert.Empty(api.Failures);
        Assert.Equal("Host=db;Username=ro", executor.ConnectionSeen);      // resolved locally, never from the job
        Assert.Equal("erp_principal", executor.Seen!.DatabaseRef);
    }

    [Theory]
    [InlineData("expired", "job_expired")]
    [InlineData("lease_expired", "lease_expired")]
    [InlineData("limits_too_big", "limits_out_of_range")]
    public async Task Unsafe_jobs_are_reported_as_rejected_and_never_executed(string vector, string code)
    {
        var (runner, api, executor) = Build();
        await runner.RunAsync(Vectors.Get(vector), CancellationToken.None);
        Assert.Null(executor.Seen);
        Assert.Equal(0, api.Started);
        var failure = Assert.Single(api.Failures);
        Assert.Equal(("REJECTED", code, false), (failure.Body.Status, failure.Body.ErrorCode, failure.Body.Retryable));
    }

    [Fact]
    public async Task A_bad_signature_or_a_job_for_another_tenant_is_dropped_without_telling_the_central_anything()
    {
        var (runner, api, executor) = Build();
        var valid = Vectors.Get("valid");
        await runner.RunAsync(new JobEnvelope { Payload = valid.Payload, Signature = Vectors.Get("expired").Signature }, CancellationToken.None);
        await runner.RunAsync(Vectors.Get("wrong_tenant"), CancellationToken.None);
        Assert.Null(executor.Seen);
        // Bad signature but addressed to this worker: closed as REJECTED. Another tenant's job: silently dropped.
        Assert.Equal("invalid_signature", Assert.Single(api.Failures).Body.ErrorCode);
    }

    [Fact]
    public async Task A_lost_lease_skips_the_job()
    {
        var (runner, api, executor) = Build();
        api.LeaseValid = false;
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Null(executor.Seen);
        Assert.Empty(api.Results);
    }

    [Fact]
    public async Task An_unconfigured_data_source_is_rejected_without_connecting()
    {
        var (runner, api, executor) = Build(connection: null);
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Null(executor.Seen);
        Assert.Equal("database_ref_not_configured", Assert.Single(api.Failures).Body.ErrorCode);
    }

    [Fact]
    public async Task Timeouts_are_retryable_and_contract_violations_are_not()
    {
        var (runner, api, executor) = Build();
        executor.Behaviour = (_, _) => throw new TimeoutException("query_timeout");
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        executor.Behaviour = (_, _) => throw new ResultContractException("schema_mismatch");
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Equal(("TIMED_OUT", "query_timeout", true), (api.Failures[0].Body.Status, api.Failures[0].Body.ErrorCode, api.Failures[0].Body.Retryable));
        Assert.Equal(("FAILED", "schema_mismatch", false), (api.Failures[1].Body.Status, api.Failures[1].Body.ErrorCode, api.Failures[1].Body.Retryable));
    }

    [Fact]
    public async Task Driver_errors_are_reported_by_type_and_code_never_by_message()
    {
        var (runner, api, executor) = Build();
        executor.Behaviour = (_, _) => throw new PostgresException("SECRET customer data in message", "ERROR", "ERROR", "42501");
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        var body = Assert.Single(api.Failures).Body;
        Assert.DoesNotContain("SECRET", body.ErrorMessage);
        Assert.DoesNotContain("SECRET", body.ErrorCode);
        Assert.Equal("PostgresException", body.ErrorMessage);
        Assert.Equal("pg_42501", body.ErrorCode);
    }

    [Fact]
    public async Task Result_delivery_is_retried_and_finally_left_to_the_central_lease()
    {
        var (runner, api, _) = Build();
        api.ResultFailuresBeforeSuccess = 2;
        await runner.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Single(api.Results);   // third attempt worked

        var (runner2, api2, _) = Build();
        api2.ResultFailuresBeforeSuccess = 99;
        await runner2.RunAsync(Vectors.Get("valid"), CancellationToken.None);
        Assert.Empty(api2.Results);   // gave up quietly: the lease expires and the central requeues the read-only job
        Assert.Empty(api2.Failures);
    }
}
