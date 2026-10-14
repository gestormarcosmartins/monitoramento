using System.Data;
using System.Data.Common;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using Microsoft.Data.SqlClient;
using Npgsql;

namespace Nexora.BusinessWorker;

public interface IQueryExecutor
{
    Task<ResultBody> ExecuteAsync(Job job, string connectionString, CancellationToken stop);
}

/// <summary>
/// Runs one verified job against the customer database with the platform limits applied: connection and command
/// timeout, a hard cancellation a little after the driver timeout, a row cap that is enforced while reading, and
/// bound parameters only. The database login itself must be read-only; this class is defence in depth.
/// </summary>
public sealed class DbQueryExecutor : IQueryExecutor
{
    private readonly WorkerOptions _options;

    public DbQueryExecutor(WorkerOptions options) => _options = options;

    public async Task<ResultBody> ExecuteAsync(Job job, string connectionString, CancellationToken stop)
    {
        var started = DateTimeOffset.UtcNow;
        var clock = Stopwatch.StartNew();
        var timeoutSeconds = job.Constraints.TimeoutSeconds;
        using var hardStop = CancellationTokenSource.CreateLinkedTokenSource(stop);
        hardStop.CancelAfter(TimeSpan.FromSeconds(timeoutSeconds + 2));
        try
        {
            await using var connection = Create(job.Dialect, Harden(job.Dialect, connectionString, timeoutSeconds));
            await connection.OpenAsync(hardStop.Token);
            await PrepareSessionAsync(connection, job.Dialect, timeoutSeconds, hardStop.Token);
            await using var command = connection.CreateCommand();
            command.CommandText = job.Query;
            command.CommandTimeout = timeoutSeconds;
            foreach (var parameter in job.Parameters) ParameterBinder.Add(command, job.Dialect, parameter.Key, parameter.Value);
            await using var reader = await command.ExecuteReaderAsync(hardStop.Token);
            var builder = new ResultBuilder(job.ColumnSchema, job.Constraints, _options.AssumeUtcForUnspecified);
            builder.ValidateColumns(Enumerable.Range(0, reader.FieldCount).Select(reader.GetName).ToList());
            var buffer = new object?[reader.FieldCount];
            while (await reader.ReadAsync(hardStop.Token))
            {
                reader.GetValues(buffer!);
                if (builder.AddRow(buffer)) continue;
                command.Cancel();  // the cap was reached and one more row exists: stop the server from producing the rest
                break;
            }
            return builder.Build(started, (int)clock.ElapsedMilliseconds);
        }
        catch (OperationCanceledException) when (!stop.IsCancellationRequested)
        {
            throw new TimeoutException("query_timeout");
        }
    }

    private static DbConnection Create(string dialect, string connectionString) =>
        dialect == "sqlserver" ? new SqlConnection(connectionString) : new NpgsqlConnection(connectionString);

    /// <summary>Encrypted transport with a validated certificate is mandatory unless explicitly relaxed in the configuration.</summary>
    internal string Harden(string dialect, string connectionString, int timeoutSeconds)
    {
        if (dialect == "sqlserver")
        {
            var builder = new SqlConnectionStringBuilder(connectionString)
            {
                ApplicationName = "Nexora Business Worker",
                ApplicationIntent = ApplicationIntent.ReadOnly,
                ConnectTimeout = Math.Min(15, timeoutSeconds),
                Pooling = false,
                Encrypt = SqlConnectionEncryptOption.Mandatory,
            };
            if (!_options.AllowUntrustedServerCertificate) builder.TrustServerCertificate = false;
            return builder.ConnectionString;
        }
        var pg = new NpgsqlConnectionStringBuilder(connectionString)
        {
            ApplicationName = "Nexora Business Worker", Timeout = Math.Min(15, timeoutSeconds),
            CommandTimeout = timeoutSeconds, Pooling = false,
        };
        if (_options.AllowPlaintextLoopbackDatabase && IsLoopback(pg.Host)) { /* local development: keep the transport as configured */ }
        else if (!_options.AllowUntrustedServerCertificate) pg.SslMode = SslMode.VerifyFull;
        else if (pg.SslMode is SslMode.Disable or SslMode.Allow or SslMode.Prefer) pg.SslMode = SslMode.Require;
        return pg.ConnectionString;
    }

    private static bool IsLoopback(string? host) =>
        host is "localhost" or "127.0.0.1" or "::1";

    private static async Task PrepareSessionAsync(DbConnection connection, string dialect, int timeoutSeconds, CancellationToken token)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = dialect == "sqlserver"
            ? "SET LOCK_TIMEOUT 5000"
            : string.Create(CultureInfo.InvariantCulture,
                $"SET default_transaction_read_only = on; SET statement_timeout = {timeoutSeconds * 1000}; SET lock_timeout = 5000");
        await command.ExecuteNonQueryAsync(token);
    }
}

/// <summary>Binds job parameters (never concatenated into SQL). ISO dates and timestamps become typed values.</summary>
public static class ParameterBinder
{
    public static void Add(DbCommand command, string dialect, string name, JsonElement value)
    {
        var parameter = command.CreateParameter();
        parameter.ParameterName = "@" + name;
        switch (value.ValueKind)
        {
            case JsonValueKind.Null: parameter.Value = DBNull.Value; break;
            case JsonValueKind.True: parameter.Value = true; break;
            case JsonValueKind.False: parameter.Value = false; break;
            case JsonValueKind.Number:
                parameter.Value = value.TryGetInt64(out var integer) ? integer : value.GetDecimal();
                break;
            case JsonValueKind.String: BindString(parameter, dialect, value.GetString() ?? ""); break;
            default: throw new JobRejectedException("unsupported_parameter");
        }
        command.Parameters.Add(parameter);
    }

    private static void BindString(DbParameter parameter, string dialect, string text)
    {
        if (DateOnly.TryParseExact(text, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out var date))
        {
            if (dialect == "sqlserver") { parameter.DbType = DbType.Date; parameter.Value = date.ToDateTime(TimeOnly.MinValue); }
            else parameter.Value = date;
        }
        else if (text.Contains('T') && DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var moment))
        {
            if (dialect == "sqlserver") { parameter.DbType = DbType.DateTimeOffset; parameter.Value = moment; }
            else parameter.Value = moment.UtcDateTime;
        }
        else parameter.Value = text;
    }
}
