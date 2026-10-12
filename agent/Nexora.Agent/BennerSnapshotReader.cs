using System.Text.Json;

namespace Nexora.Agent;

public static class BennerSnapshotReader
{
    private static string? Text(JsonElement element, params string[] names)
    {
        foreach (var name in names)
            if (element.ValueKind == JsonValueKind.Object && element.TryGetProperty(name, out var value))
                return value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString();
        return null;
    }

    public static async Task<SnapshotEnvelope?> ReadAsync(string path, AgentConfiguration config, CancellationToken cancellationToken)
    {
        using var document = JsonDocument.Parse(await File.ReadAllTextAsync(path, cancellationToken));
        var root = document.RootElement;
        var checks = root.TryGetProperty("checks", out var list) && list.ValueKind == JsonValueKind.Array
            ? list : root.TryGetProperty("items", out list) && list.ValueKind == JsonValueKind.Array ? list : default;
        if (checks.ValueKind != JsonValueKind.Array) return null;
        var monitorByCheck = config.Monitors
            .Select(m => new { Monitor = m, Check = Text(m.Configuration, "check_id", "checkId", "code") })
            .Where(x => !string.IsNullOrWhiteSpace(x.Check))
            .ToDictionary(x => x.Check!, x => x.Monitor, StringComparer.OrdinalIgnoreCase);
        var results = new List<AgentResult>();
        foreach (var check in checks.EnumerateArray())
        {
            var checkId = Text(check, "id", "check_id", "code", "number");
            if (checkId is null || !monitorByCheck.TryGetValue(checkId, out var monitor)) continue;
            var state = (Text(check, "state", "status", "level") ?? "error").ToLowerInvariant();
            var ok = state is "ok" or "success" or "normal" or "info";
            var message = Text(check, "message", "summary", "result", "title") ?? $"Diagnóstico {checkId}: {state}";
            results.Add(new AgentResult(monitor.Id, ok, null, message, DateTimeOffset.UtcNow));
        }
        if (results.Count == 0) return null;
        var collected = DateTimeOffset.UtcNow;
        if (DateTimeOffset.TryParse(Text(root, "collected_at", "generated_at", "timestamp"), out var parsed)) collected = parsed;
        return new SnapshotEnvelope(Path.GetFileNameWithoutExtension(path), collected, results,
            new Dictionary<string, object?> { ["source"] = "benner-health-json", ["file"] = Path.GetFileName(path) });
    }
}
