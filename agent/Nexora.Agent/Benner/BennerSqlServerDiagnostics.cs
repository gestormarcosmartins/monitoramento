using System.Diagnostics;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace Nexora.Agent.Benner;

/// <summary>SQL-Server implementation of <see cref="IBennerEngineDiagnostics"/>: runs the 19
/// ported checks sequentially and builds the full payload that AgentWorker then POSTs to
/// Nexora's /api/v1/agents/benner/snapshots. Mirrors Python's run_collection() orchestration
/// (app.py lines ~1218-1262) field-for-field.</summary>
public sealed class BennerSqlServerDiagnostics : IBennerEngineDiagnostics
{
    private readonly AgentOptions _options;
    private readonly SqlCredentialStore _password;
    private readonly ILogger<BennerSqlServerDiagnostics> _logger;

    public BennerSqlServerDiagnostics(IOptions<AgentOptions> options, SqlCredentialStore password, ILogger<BennerSqlServerDiagnostics> logger)
    {
        _options = options.Value;
        _password = password;
        _logger = logger;
    }

    private BennerThresholds Thresholds() => new()
    {
        QueryTimeoutSeconds = _options.BennerQueryTimeoutSeconds,
        LatencySampleSeconds = _options.BennerLatencySampleSeconds,
        IoLatencyMinOps = _options.BennerIoLatencyMinOps,
        BackupFullMaxAgeHours = _options.BennerBackupFullMaxAgeHours,
        BackupLogMaxGapMinutes = _options.BennerBackupLogMaxGapMinutes,
        BackupLogLookbackHours = _options.BennerBackupLogLookbackHours,
        DiskFreeWarnPct = _options.BennerDiskFreeWarnPct,
        DiskFreeCritPct = _options.BennerDiskFreeCritPct,
        TempdbUsedWarnPct = _options.BennerTempdbUsedWarnPct,
        TempdbVersionWarnGb = _options.BennerTempdbVersionWarnGb,
        TempdbVersionCritGb = _options.BennerTempdbVersionCritGb,
        LongTranWarnMinutes = _options.BennerLongTranWarnMinutes,
        LongTranCritMinutes = _options.BennerLongTranCritMinutes,
        MemAvailableWarnMb = _options.BennerMemAvailableWarnMb,
        MemAvailableCritMb = _options.BennerMemAvailableCritMb,
        BlockWarnSeconds = _options.BennerBlockWarnSeconds,
        BlockCritSeconds = _options.BennerBlockCritSeconds,
    };

    // DESIGN DECISION (connection target): reuses the same SQL Server instance/credentials the
    // rest of the agent already monitors (AgentOptions.SqlServer/SqlDatabase/SqlUser/SqlEncrypt/
    // SqlTrustServerCertificate + SqlCredentialStore), matching the Python collector's own
    // default (.env.example ships DB_SERVER=<the Benner box>, DB_DATABASE=master - the same
    // shape as this agent's existing SqlServer/SqlDatabase settings). Nothing in the Python
    // source implies Benner ever targets a different instance than the one SqlServerCollector
    // already connects to, so a separate BennerServer/BennerDatabase override was deliberately
    // NOT added - it would just be a second set of connection settings to keep in sync for no
    // behavioral gain. If a future deployment needs Benner checks against a different instance,
    // add that override here without touching SqlServerCollector.
    private SqlConnection Connection()
    {
        var builder = new SqlConnectionStringBuilder
        {
            DataSource = _options.SqlServer, InitialCatalog = _options.SqlDatabase, IntegratedSecurity = _options.SqlIntegratedSecurity,
            Encrypt = _options.SqlEncrypt, TrustServerCertificate = _options.SqlTrustServerCertificate,
            ConnectTimeout = Math.Clamp(_options.SqlCommandTimeoutSeconds, 5, 120), ApplicationName = "Nexora Agent (Benner)",
        };
        if (!_options.SqlIntegratedSecurity) { builder.UserID = _options.SqlUser; builder.Password = _password.Load(); }
        return new SqlConnection(builder.ConnectionString);
    }

    // Opens a brand-new connection per check/query, matching Python's execute_sql() (which
    // opens and closes a fresh pyodbc connection per check instead of reusing one across the
    // whole collection). This keeps behavioral fidelity with the original and avoids one slow
    // check (e.g. 4/5's WAITFOR sampling window) holding a connection the others could use.
    private async Task<List<List<Dictionary<string, object?>>>> ExecuteMultiAsync(string sql, int timeoutSeconds, CancellationToken token)
    {
        await using var connection = Connection();
        await connection.OpenAsync(token);
        await using var command = new SqlCommand(sql, connection) { CommandTimeout = Math.Max(1, timeoutSeconds) };
        await using var reader = await command.ExecuteReaderAsync(token);
        var resultSets = new List<List<Dictionary<string, object?>>>();
        do
        {
            var rows = new List<Dictionary<string, object?>>();
            if (reader.FieldCount > 0)
            {
                while (await reader.ReadAsync(token))
                {
                    var row = new Dictionary<string, object?>();
                    for (var i = 0; i < reader.FieldCount; i++)
                        row[reader.GetName(i)] = reader.IsDBNull(i) ? null : reader.GetValue(i);
                    rows.Add(row);
                }
            }
            resultSets.Add(rows);
        } while (await reader.NextResultAsync(token));
        return resultSets;
    }

    public async Task<BennerSnapshotPayload?> CollectAsync(CancellationToken cancellationToken)
    {
        if (!_options.SqlEnabled || !_options.BennerEnabled) return null;
        var thresholds = Thresholds();
        var stopwatchAll = Stopwatch.StartNew();
        var timestamp = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss");
        var items = new List<BennerItem>();
        var summary = new BennerSummary();

        foreach (var check in BennerCheckDefinitions.All)
        {
            var sql = check.BuildSql(thresholds);
            var info = new BennerCheckInfo { Id = check.Id, Name = check.Name, Category = check.Category, Description = check.Description, Sql = sql };
            var stopwatch = Stopwatch.StartNew();
            try
            {
                var resultSets = await ExecuteMultiAsync(sql, thresholds.QueryTimeoutSeconds, cancellationToken);
                var severity = BennerSeverity.SeverityFor(check.Id, resultSets, thresholds);
                summary.Increment(severity);
                items.Add(new BennerItem
                {
                    Check = info, Status = "success", Severity = severity,
                    DurationMs = Math.Round(stopwatch.Elapsed.TotalMilliseconds, 2), ResultSets = resultSets,
                });
            }
            catch (Exception error) when (error is not OperationCanceledException)
            {
                summary.Increment("error");
                items.Add(new BennerItem
                {
                    Check = info, Status = "error", Severity = "error",
                    DurationMs = Math.Round(stopwatch.Elapsed.TotalMilliseconds, 2), Error = error.Message,
                    ResultSets = new List<List<Dictionary<string, object?>>>(),
                });
                _logger.LogWarning(error, "Falha no diagnóstico Benner #{CheckId} ({Name}).", check.Id, check.Name);
            }
        }

        var payload = new BennerSnapshotPayload
        {
            SchemaVersion = 1,
            ExecutionId = Guid.NewGuid().ToString(),
            Timestamp = timestamp,
            FinishedAt = DateTime.Now.ToString("yyyy-MM-ddTHH:mm:ss"),
            Target = new BennerTarget
            {
                Server = _options.SqlServer,
                Database = _options.SqlDatabase,
                // Python reports the ODBC driver name (e.g. "ODBC Driver 18 for SQL Server")
                // here; this agent connects through Microsoft.Data.SqlClient's native TDS
                // implementation rather than ODBC, so there is no equivalent driver name -
                // reporting the .NET client name is the closest honest equivalent.
                Driver = "Microsoft.Data.SqlClient",
                QueryTimeoutSeconds = thresholds.QueryTimeoutSeconds,
            },
            DurationMs = Math.Round(stopwatchAll.Elapsed.TotalMilliseconds, 2),
            Summary = summary,
            Items = items,
        };
        payload.AttentionPoints = BennerSeverity.AttentionPoints(items);
        payload.Metrics = BennerSeverity.DashboardMetrics(items);
        payload.Backup = BennerSeverity.BackupFromItems(items, thresholds);
        (payload.LogFile, payload.AttentionLogFile) = await WriteLocalArchiveAsync(payload, thresholds, cancellationToken);
        return payload;
    }

    private static string BaseDirectory(AgentOptions options) => Path.Combine(AgentPaths.DataDirectory(options), "benner", "logs");

    private static readonly JsonSerializerOptions ArchiveJsonOptions = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    // Local-only archive, analogous to Python's write_log()/write_attention_log(): best-effort,
    // never throws - a failure to write the on-disk archive must not block sending the payload
    // to Nexora (the whole point of this local copy is forensic/debugging convenience).
    private async Task<(string LogFile, string AttentionLogFile)> WriteLocalArchiveAsync(
        BennerSnapshotPayload payload, BennerThresholds thresholds, CancellationToken token)
    {
        var baseDirectory = BaseDirectory(_options);
        var now = DateTime.Now;
        string logFile = "", attentionLogFile = "";
        try
        {
            var archiveDirectory = Path.Combine(baseDirectory, "archive", now.ToString("yyyy"), now.ToString("MM"));
            Directory.CreateDirectory(archiveDirectory);
            var stamp = now.ToString("yyyyMMdd_HHmmss_ffffff");
            var fileName = $"benner_health_{stamp}.json";
            var path = Path.Combine(archiveDirectory, fileName);
            var temporary = path + ".tmp";
            await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(payload, ArchiveJsonOptions), token);
            File.Move(temporary, path, overwrite: true);
            logFile = Path.GetRelativePath(baseDirectory, path).Replace('\\', '/');

            try
            {
                attentionLogFile = await WriteAttentionLogAsync(payload, baseDirectory, archiveDirectory, stamp, logFile, thresholds, token);
            }
            catch (Exception error)
            {
                _logger.LogWarning(error, "Falha ao gravar o log de atenção (queries/índices) do Benner.");
            }
        }
        catch (Exception error)
        {
            _logger.LogWarning(error, "Falha ao gravar o arquivo local de diagnóstico Benner.");
        }
        return (logFile, attentionLogFile);
    }

    private sealed class AttentionCheckInfo
    {
        [JsonPropertyName("id")] public int Id { get; set; }
        [JsonPropertyName("name")] public string Name { get; set; } = "";
        [JsonPropertyName("category")] public string Category { get; set; } = "";
        [JsonPropertyName("description")] public string Description { get; set; } = "";
    }

    private sealed class AttentionQueryEntry
    {
        [JsonPropertyName("check")] public AttentionCheckInfo Check { get; set; } = new();
        [JsonPropertyName("status")] public string Status { get; set; } = "";
        [JsonPropertyName("error")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public string? Error { get; set; }
        [JsonPropertyName("result_sets")] public List<List<Dictionary<string, object?>>> ResultSets { get; set; } = new();
    }

    private sealed class MissingIndexRecommendations
    {
        [JsonPropertyName("status")] public string Status { get; set; } = "";
        [JsonPropertyName("error")]
        [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
        public string? Error { get; set; }
        [JsonPropertyName("rows")] public List<Dictionary<string, object?>> Rows { get; set; } = new();
    }

    private sealed class AttentionLogDocument
    {
        [JsonPropertyName("schema_version")] public int SchemaVersion { get; set; } = 1;
        [JsonPropertyName("timestamp")] public string Timestamp { get; set; } = "";
        [JsonPropertyName("execution_id")] public string ExecutionId { get; set; } = "";
        [JsonPropertyName("collection_file")] public string CollectionFile { get; set; } = "";
        [JsonPropertyName("top_queries")] public List<AttentionQueryEntry> TopQueries { get; set; } = new();
        [JsonPropertyName("missing_index_recommendations")] public MissingIndexRecommendations MissingIndexRecommendations { get; set; } = new();
    }

    // Separate, local-only file for investigating heavy queries/missing indexes - re-packages
    // checks 6/7/8 (without their SQL text, like Python's public_check()) plus a 4th query
    // (MISSING_INDEX_SQL) that is NOT one of the 19 checks and is never sent to Nexora; only
    // this file's relative path (attention_log_file) travels in the payload, never its content.
    private async Task<string> WriteAttentionLogAsync(
        BennerSnapshotPayload payload, string baseDirectory, string archiveDirectory, string stamp, string logFile,
        BennerThresholds thresholds, CancellationToken token)
    {
        var document = new AttentionLogDocument
        {
            SchemaVersion = 1, Timestamp = payload.Timestamp, ExecutionId = payload.ExecutionId, CollectionFile = logFile,
        };
        foreach (var item in payload.Items)
        {
            if (item.Check.Id is not (6 or 7 or 8)) continue;
            document.TopQueries.Add(new AttentionQueryEntry
            {
                Check = new AttentionCheckInfo { Id = item.Check.Id, Name = item.Check.Name, Category = item.Check.Category, Description = item.Check.Description },
                Status = item.Status, Error = item.Error, ResultSets = item.ResultSets,
            });
        }
        try
        {
            var rows = (await ExecuteMultiAsync(BennerCheckDefinitions.MissingIndexSql, thresholds.QueryTimeoutSeconds, token)).FirstOrDefault() ?? new();
            document.MissingIndexRecommendations = new MissingIndexRecommendations { Status = "success", Rows = rows };
        }
        catch (Exception error)
        {
            document.MissingIndexRecommendations = new MissingIndexRecommendations { Status = "error", Error = error.Message, Rows = new() };
        }
        var path = Path.Combine(archiveDirectory, $"attention_queries_indices_{stamp}.json");
        var temporary = path + ".tmp";
        await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(document, ArchiveJsonOptions), token);
        File.Move(temporary, path, overwrite: true);
        return Path.GetRelativePath(baseDirectory, path).Replace('\\', '/');
    }
}
