using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Nexora.BusinessWorker;

namespace Nexora.Agent;

/// <summary>Versão real do agente (a do assembly). Antes era uma string fixa no heartbeat, o que impediria saber o que está instalado.</summary>
public static class AgentVersion
{
    public static string Current => typeof(AgentVersion).Assembly.GetName().Version?.ToString(3) ?? "0.0.0";
}

/// <summary>O que a Central assinou: preso a esta empresa, a este agente e a esta plataforma, com o hash e o tamanho exatos do pacote.</summary>
public sealed class AgentUpdateManifest
{
    [JsonPropertyName("type")] public string Type { get; init; } = "";
    [JsonPropertyName("tenant_id")] public Guid TenantId { get; init; }
    [JsonPropertyName("agent_id")] public Guid AgentId { get; init; }
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("platform")] public string Platform { get; init; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; init; } = "";
    [JsonPropertyName("size_bytes")] public long SizeBytes { get; init; }
    [JsonPropertyName("rollback")] public bool Rollback { get; init; }
    [JsonPropertyName("issued_at")] public DateTimeOffset IssuedAt { get; init; }
    [JsonPropertyName("expires_at")] public DateTimeOffset ExpiresAt { get; init; }
}

/// <summary>O que fica ao lado do pacote para o script de aplicação (e para --verify-staged).</summary>
public sealed class PendingAgentUpdate
{
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("platform")] public string Platform { get; init; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; init; } = "";
    [JsonPropertyName("payload")] public string Payload { get; init; } = "";
    [JsonPropertyName("signature")] public string Signature { get; init; } = "";
    [JsonPropertyName("staged_at")] public DateTimeOffset StagedAt { get; init; }
}

/// <summary>Resultado da última aplicação, gravado pelo script e reportado à Central pelo agente na partida seguinte.</summary>
public sealed class AgentApplyOutcome
{
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("result")] public string Result { get; init; } = "";
    [JsonPropertyName("error")] public string Error { get; init; } = "";
}

/// <summary>Verificação pura (sem rede, sem disco além do pacote preparado). Reaproveita a assinatura Ed25519 do Business Worker.</summary>
public static class AgentUpdateVerifier
{
    private static readonly Regex HashPattern = new("^[0-9a-f]{64}$", RegexOptions.Compiled);

    /// <summary>Verifica o envelope e devolve o manifesto, ou lança com um código estável.</summary>
    public static AgentUpdateManifest VerifyManifest(string payload, string signature, string publicKey, Guid tenant, Guid agent,
        string currentVersion, string platform, DateTimeOffset now, bool staged)
    {
        if (!JobVerifier.VerifySignature(payload, signature, publicKey)) throw new UpdateRejectedException("invalid_signature");
        AgentUpdateManifest? manifest;
        try { manifest = JsonSerializer.Deserialize<AgentUpdateManifest>(payload); }
        catch (JsonException) { throw new UpdateRejectedException("invalid_manifest"); }
        if (manifest is null || manifest.Type != "agent_update") throw new UpdateRejectedException("invalid_manifest");
        if (manifest.TenantId != tenant) throw new UpdateRejectedException("tenant_mismatch");
        if (manifest.AgentId != agent) throw new UpdateRejectedException("agent_mismatch");
        if (platform.Length == 0 || manifest.Platform != platform) throw new UpdateRejectedException("platform_mismatch");
        if (!UpdateStager.TryParseVersion(manifest.Version, out var target)) throw new UpdateRejectedException("invalid_version");
        if (!HashPattern.IsMatch(manifest.Sha256)) throw new UpdateRejectedException("invalid_hash");
        if (manifest.SizeBytes is < 1 or > UpdateStager.MaxPackageBytes) throw new UpdateRejectedException("invalid_size");
        // um pacote preparado pode esperar a troca; uma oferta tem de ser fresca. Em ambos os casos um manifesto antigo repetido morre pela idade.
        if (staged ? now - manifest.IssuedAt > UpdateStager.StagedValidity : now > manifest.ExpiresAt) throw new UpdateRejectedException("manifest_expired");
        if (!UpdateStager.TryParseVersion(currentVersion, out var current)) throw new UpdateRejectedException("unknown_current_version");
        if (manifest.Rollback ? target == current : target.CompareTo(current) <= 0) throw new UpdateRejectedException("not_an_update");
        return manifest;
    }

    /// <summary>
    /// Reverificação feita pelo script de aplicação, pelo binário ATUALMENTE instalado (o confiável): assinatura, vínculo, hash e tamanho
    /// do pacote preparado. Devolve "ok" ou um código de falha estável.
    /// </summary>
    public static string VerifyStaged(string readyDirectory, string publicKey, Guid tenant, Guid agent, string currentVersion, string platform, DateTimeOffset now)
    {
        try
        {
            var pendingPath = Path.Combine(readyDirectory, "pending.json");
            var packagePath = Path.Combine(readyDirectory, "package.pkg");
            if (!File.Exists(pendingPath) || !File.Exists(packagePath)) return "nothing_staged";
            var pending = JsonSerializer.Deserialize<PendingAgentUpdate>(File.ReadAllText(pendingPath));
            if (pending is null) return "invalid_pending";
            var manifest = VerifyManifest(pending.Payload, pending.Signature, publicKey, tenant, agent, currentVersion, platform, now, staged: true);
            if (manifest.Version != pending.Version || manifest.Sha256 != pending.Sha256) return "pending_mismatch";
            var info = new FileInfo(packagePath);
            if (info.Length != manifest.SizeBytes) return "size_mismatch";
            using var stream = info.OpenRead();
            return UpdateStager.Sha256Hex(stream) == manifest.Sha256 ? "ok" : "hash_mismatch";
        }
        catch (UpdateRejectedException rejected) { return rejected.Code; }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException) { return "unreadable"; }
    }
}
