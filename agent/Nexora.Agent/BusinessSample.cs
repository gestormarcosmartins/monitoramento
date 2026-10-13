using System.Text.Json.Serialization;
namespace Nexora.Agent;
public sealed record BusinessSample(
    [property:JsonPropertyName("indicator_id")] string IndicatorId,
    [property:JsonPropertyName("event_id")] string EventId,
    [property:JsonPropertyName("value")] decimal Value,
    [property:JsonPropertyName("collected_at")] DateTimeOffset CollectedAt,
    [property:JsonPropertyName("collector_id")] string? CollectorId = null,
    [property:JsonPropertyName("config_revision")] int? ConfigRevision = null);
