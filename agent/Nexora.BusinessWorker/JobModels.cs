using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace Nexora.BusinessWorker;

public sealed class JobEnvelope
{
    [JsonPropertyName("payload")] public string Payload { get; init; } = "";
    [JsonPropertyName("signature")] public string Signature { get; init; } = "";
}

public sealed class ClaimResponse
{
    [JsonPropertyName("jobs")] public List<JobEnvelope> Jobs { get; init; } = new();
}

public sealed class ColumnSpec
{
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("type")] public string Type { get; init; } = "";
}

public sealed class JobConstraints
{
    [JsonPropertyName("timeout_seconds")] public int TimeoutSeconds { get; init; } = 15;
    [JsonPropertyName("max_rows")] public int MaxRows { get; init; } = 500;
    [JsonPropertyName("max_bytes")] public int MaxBytes { get; init; } = 5 * 1024 * 1024;
}

/// <summary>A job parsed from a payload whose signature has ALREADY been verified. Never built from unverified bytes.</summary>
public sealed class Job
{
    [JsonPropertyName("execution_id")] public Guid ExecutionId { get; init; }
    [JsonPropertyName("monitor_id")] public Guid MonitorId { get; init; }
    [JsonPropertyName("revision")] public int Revision { get; init; }
    [JsonPropertyName("tenant_id")] public Guid TenantId { get; init; }
    [JsonPropertyName("worker_id")] public Guid WorkerId { get; init; }
    [JsonPropertyName("expires_at")] public DateTimeOffset ExpiresAt { get; init; }
    [JsonPropertyName("lease_expires_at")] public DateTimeOffset LeaseExpiresAt { get; init; }
    [JsonPropertyName("database_ref")] public string DatabaseRef { get; init; } = "";
    [JsonPropertyName("dialect")] public string Dialect { get; init; } = "";
    [JsonPropertyName("query")] public string Query { get; init; } = "";
    [JsonPropertyName("parameters")] public Dictionary<string, JsonElement> Parameters { get; init; } = new();
    [JsonPropertyName("constraints")] public JobConstraints Constraints { get; init; } = new();
    [JsonPropertyName("column_schema")] public List<ColumnSpec> ColumnSchema { get; init; } = new();
    [JsonPropertyName("test")] public bool Test { get; init; }
    [JsonPropertyName("correlation_id")] public Guid CorrelationId { get; init; }
}

public sealed class ResultBody
{
    [JsonPropertyName("status")] public string Status { get; init; } = "SUCCEEDED";
    [JsonPropertyName("started_at")] public DateTimeOffset StartedAt { get; set; }
    [JsonPropertyName("duration_ms")] public int DurationMs { get; set; }
    [JsonPropertyName("columns")] public List<ColumnSpec> Columns { get; init; } = new();
    [JsonPropertyName("rows")] public List<JsonObject> Rows { get; init; } = new();
    [JsonPropertyName("row_count")] public int RowCount { get; init; }
    [JsonPropertyName("truncated")] public bool Truncated { get; init; }
}

public sealed class FailureBody
{
    [JsonPropertyName("status")] public string Status { get; init; } = "FAILED";
    [JsonPropertyName("error_code")] public string ErrorCode { get; init; } = "unknown_error";
    [JsonPropertyName("error_message")] public string ErrorMessage { get; init; } = "";
    [JsonPropertyName("retryable")] public bool Retryable { get; init; }
    [JsonPropertyName("duration_ms")] public int? DurationMs { get; init; }
}

/// <summary>The job cannot (or must not) run here: bad signature, wrong tenant/worker, expired, unknown data source.</summary>
public sealed class JobRejectedException : Exception
{
    public JobRejectedException(string code) : base(code) { Code = code; }
    public string Code { get; }
}

/// <summary>The result would exceed what the platform allows (size, column mismatch...): a failure of this run, not a retry case.</summary>
public sealed class ResultContractException : Exception
{
    public ResultContractException(string code) : base(code) { Code = code; }
    public string Code { get; }
}
