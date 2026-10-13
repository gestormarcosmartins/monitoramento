using System.Text.Json.Serialization;

namespace Nexora.Agent.Benner;

// POCOs mirroring, field-for-field, the JSON payload produced by the legacy Python
// "benner_health_monitor" (app.py: run_collection()) and expected verbatim by the
// Nexora backend's POST /api/v1/agents/benner/snapshots ingestion (benner.py).
// Field names are snake_case via JsonPropertyName so the wire shape matches exactly
// what benner.py already parses - do not rename these without updating benner.py too.

/// <summary>Configurable thresholds mirroring Python's alert_thresholds()/backup_thresholds()
/// plus the few values baked into the SQL templates (I/O sample window, query timeout).
/// All defaults match the Python env-var defaults verbatim (see AgentOptions.cs).</summary>
public sealed class BennerThresholds
{
    public int QueryTimeoutSeconds { get; init; } = 60;
    // Python: min(30, DB_LATENCY_SAMPLE_SECONDS) - hard-capped at 30 regardless of configuration.
    public int LatencySampleSeconds { get; init; } = 5;
    public int IoLatencyMinOps { get; init; } = 20;
    public int BackupFullMaxAgeHours { get; init; } = 30;
    public int BackupLogMaxGapMinutes { get; init; } = 45;
    public int BackupLogLookbackHours { get; init; } = 24;
    public int DiskFreeWarnPct { get; init; } = 15;
    public int DiskFreeCritPct { get; init; } = 10;
    public int TempdbUsedWarnPct { get; init; } = 90;
    public int TempdbVersionWarnGb { get; init; } = 5;
    public int TempdbVersionCritGb { get; init; } = 20;
    public int LongTranWarnMinutes { get; init; } = 15;
    public int LongTranCritMinutes { get; init; } = 60;
    public int MemAvailableWarnMb { get; init; } = 4096;
    public int MemAvailableCritMb { get; init; } = 2048;
    public int BlockWarnSeconds { get; init; } = 5;
    public int BlockCritSeconds { get; init; } = 30;

    public int EffectiveLatencySampleSeconds => Math.Min(30, Math.Max(1, LatencySampleSeconds));
}

/// <summary>A single check definition (id/name/category/description/sql), equivalent to one
/// entry of Python's CHECKS list. Checks 4, 5, 15 and 18 build their SQL dynamically from the
/// current thresholds (SqlFactory); the rest carry a fixed SQL string (StaticSql).</summary>
public sealed class BennerCheckDefinition
{
    public required int Id { get; init; }
    public required string Name { get; init; }
    public required string Category { get; init; }
    public required string Description { get; init; }
    public string? StaticSql { get; init; }
    public Func<BennerThresholds, string>? SqlFactory { get; init; }

    public string BuildSql(BennerThresholds thresholds) =>
        (SqlFactory is not null ? SqlFactory(thresholds) : StaticSql ?? "").Trim();
}

public sealed class BennerCheckInfo
{
    [JsonPropertyName("id")] public int Id { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    // Full SQL text, verbatim - Nexora's "Diagnóstico" tab displays this per check,
    // matching Python's logged_check() (which embeds "sql" unlike public_check()).
    [JsonPropertyName("sql")] public string Sql { get; set; } = "";
}

public sealed class BennerItem
{
    [JsonPropertyName("check")] public BennerCheckInfo Check { get; set; } = new();
    // "success" | "error" - kept literal (not the finer severity) because Nexora's
    // benner.py historically read this field for up/down status.
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    // "ok" | "warning" | "critical" | "info" | "error"
    [JsonPropertyName("severity")] public string Severity { get; set; } = "";
    [JsonPropertyName("duration_ms")] public double DurationMs { get; set; }
    [JsonPropertyName("result_sets")] public List<List<Dictionary<string, object?>>> ResultSets { get; set; } = new();
    [JsonPropertyName("error")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Error { get; set; }
}

public sealed class BennerTarget
{
    [JsonPropertyName("server")] public string Server { get; set; } = "";
    [JsonPropertyName("database")] public string Database { get; set; } = "";
    [JsonPropertyName("driver")] public string Driver { get; set; } = "";
    [JsonPropertyName("query_timeout_seconds")] public int QueryTimeoutSeconds { get; set; }
}

public sealed class BennerSummary
{
    [JsonPropertyName("ok")] public int Ok { get; set; }
    [JsonPropertyName("warning")] public int Warning { get; set; }
    [JsonPropertyName("critical")] public int Critical { get; set; }
    [JsonPropertyName("info")] public int Info { get; set; }
    [JsonPropertyName("error")] public int Error { get; set; }

    public void Increment(string severity)
    {
        switch (severity)
        {
            case "ok": Ok++; break;
            case "warning": Warning++; break;
            case "critical": Critical++; break;
            case "info": Info++; break;
            default: Error++; break;
        }
    }
}

public sealed class BennerAttentionPoint
{
    [JsonPropertyName("check_id")] public int CheckId { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("severity")] public string Severity { get; set; } = "";
    [JsonPropertyName("metric")] public string Metric { get; set; } = "";
    [JsonPropertyName("value")] public double? Value { get; set; }
    [JsonPropertyName("unit")] public string Unit { get; set; } = "";
    [JsonPropertyName("lower_is_worse")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public bool LowerIsWorse { get; set; }
    [JsonPropertyName("detail")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Detail { get; set; }
}

public sealed class BennerMetric
{
    [JsonPropertyName("check_id")] public int CheckId { get; set; }
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("unit")] public string Unit { get; set; } = "";
    [JsonPropertyName("higher_is_worse")] public bool HigherIsWorse { get; set; }
    [JsonPropertyName("value")] public double? Value { get; set; }
    [JsonPropertyName("status")] public string Status { get; set; } = "";
}

public sealed class BennerBackupBlock
{
    [JsonPropertyName("label")] public string Label { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("ok")] public int Ok { get; set; }
    [JsonPropertyName("total")] public int Total { get; set; }
    [JsonPropertyName("failing")] public List<string> Failing { get; set; } = new();
}

public sealed class BennerBackupThresholds
{
    [JsonPropertyName("full_max_age_hours")] public int FullMaxAgeHours { get; set; }
    [JsonPropertyName("log_max_gap_minutes")] public int LogMaxGapMinutes { get; set; }
    [JsonPropertyName("log_lookback_hours")] public int LogLookbackHours { get; set; }
}

/// <summary>Mirrors Python's backup_from_items()/backup_summary(): either a populated
/// summary, an {"error": "..."} shape (check 15 failed), or null (check 15 missing).</summary>
public sealed class BennerBackupSummary
{
    [JsonPropertyName("full")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public BennerBackupBlock? Full { get; set; }
    [JsonPropertyName("logs")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public BennerBackupBlock? Logs { get; set; }
    [JsonPropertyName("thresholds")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public BennerBackupThresholds? Thresholds { get; set; }
    [JsonPropertyName("databases")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public List<Dictionary<string, object?>>? Databases { get; set; }
    [JsonPropertyName("error")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? Error { get; set; }
}

public sealed class BennerSnapshotPayload
{
    [JsonPropertyName("schema_version")] public int SchemaVersion { get; set; } = 1;
    [JsonPropertyName("execution_id")] public string ExecutionId { get; set; } = "";
    [JsonPropertyName("timestamp")] public string Timestamp { get; set; } = "";
    [JsonPropertyName("finished_at")] public string FinishedAt { get; set; } = "";
    [JsonPropertyName("target")] public BennerTarget Target { get; set; } = new();
    [JsonPropertyName("duration_ms")] public double DurationMs { get; set; }
    [JsonPropertyName("summary")] public BennerSummary Summary { get; set; } = new();
    [JsonPropertyName("items")] public List<BennerItem> Items { get; set; } = new();
    [JsonPropertyName("attention_points")] public List<BennerAttentionPoint> AttentionPoints { get; set; } = new();
    [JsonPropertyName("metrics")] public List<BennerMetric> Metrics { get; set; } = new();
    // Always present in the JSON (as null when absent), matching Python's payload["backup"] =
    // None - Nexora's ingestion treats a missing key and an explicit null identically either way
    // (payload.get("backup")), but keeping the key is closer to the original wire shape.
    [JsonPropertyName("backup")] public BennerBackupSummary? Backup { get; set; }
    [JsonPropertyName("log_file")] public string LogFile { get; set; } = "";
    [JsonPropertyName("attention_log_file")] public string AttentionLogFile { get; set; } = "";
}
