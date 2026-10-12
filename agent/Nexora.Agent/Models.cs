using System.Text.Json;
using System.Text.Json.Serialization;

namespace Nexora.Agent;

public sealed record Credentials(
    [property: JsonPropertyName("agent_id")] string AgentId,
    [property: JsonPropertyName("agent_token")] string AgentToken,
    [property: JsonPropertyName("tenant_id")] string TenantId);

public sealed record RemoteMonitor(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("configuration")] JsonElement Configuration);

public sealed record ChaveouServiceState(
    string Code,
    string Name,
    string State,
    string? ResultCode,
    int? LatencyMs,
    DateTimeOffset? CheckedAt);

public sealed record AgentConfiguration(
    [property: JsonPropertyName("monitors")] List<RemoteMonitor> Monitors,
    [property: JsonPropertyName("configuration")] JsonElement Configuration);

public sealed record AgentResult(
    [property: JsonPropertyName("monitor_id")] string MonitorId,
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("status")] string? Status,
    [property: JsonPropertyName("message")] string Message,
    [property: JsonPropertyName("collected_at")] DateTimeOffset CollectedAt);

public sealed record SnapshotEnvelope(
    [property: JsonPropertyName("execution_id")] string ExecutionId,
    [property: JsonPropertyName("collected_at")] DateTimeOffset CollectedAt,
    [property: JsonPropertyName("results")] List<AgentResult> Results,
    [property: JsonPropertyName("metadata")] Dictionary<string, object?> Metadata);
