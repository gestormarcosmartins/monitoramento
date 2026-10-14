using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;

namespace Nexora.BusinessWorker;

public sealed class UpdateRejectedException : Exception
{
    public UpdateRejectedException(string code) : base(code) { Code = code; }
    public string Code { get; }
}

/// <summary>What the central signed: bound to this tenant, worker and platform, with the exact hash and size of the package.</summary>
public sealed class UpdateManifest
{
    [JsonPropertyName("type")] public string Type { get; init; } = "";
    [JsonPropertyName("tenant_id")] public Guid TenantId { get; init; }
    [JsonPropertyName("worker_id")] public Guid WorkerId { get; init; }
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("platform")] public string Platform { get; init; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; init; } = "";
    [JsonPropertyName("size_bytes")] public long SizeBytes { get; init; }
    [JsonPropertyName("rollback")] public bool Rollback { get; init; }
    [JsonPropertyName("issued_at")] public DateTimeOffset IssuedAt { get; init; }
    [JsonPropertyName("expires_at")] public DateTimeOffset ExpiresAt { get; init; }
}

public sealed class UpdateOffer
{
    [JsonPropertyName("update")] public JobEnvelope? Update { get; init; }
}

/// <summary>What the stager leaves for the installer-side apply script (and for <c>--verify-staged</c>).</summary>
public sealed class PendingUpdate
{
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("platform")] public string Platform { get; init; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; init; } = "";
    [JsonPropertyName("payload")] public string Payload { get; init; } = "";
    [JsonPropertyName("signature")] public string Signature { get; init; } = "";
    [JsonPropertyName("staged_at")] public DateTimeOffset StagedAt { get; init; }
}

/// <summary>
/// Remote update, worker side: ask, VERIFY, download, check size and SHA-256, and only STAGE the package. Nothing here replaces
/// the running binary: applying (swap, probation, rollback) is done by the installer scripts after a restart, and re-verifies
/// the same signed manifest with <see cref="VerifyStaged"/> before touching anything.
/// </summary>
public sealed class UpdateStager
{
    public const long MaxPackageBytes = 200L * 1024 * 1024;
    public static readonly TimeSpan StagedValidity = TimeSpan.FromHours(24);
    private static readonly Regex VersionPattern = new(@"^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$", RegexOptions.Compiled);
    private static readonly Regex HashPattern = new("^[0-9a-f]{64}$", RegexOptions.Compiled);
    private readonly ICentralApi _api;
    private readonly WorkerOptions _options;
    private readonly ILogger<UpdateStager> _logger;
    private readonly TimeProvider _time;

    public UpdateStager(ICentralApi api, WorkerOptions options, ILogger<UpdateStager> logger, TimeProvider? time = null)
    {
        _api = api; _options = options; _logger = logger; _time = time ?? TimeProvider.System;
    }

    public static string CurrentPlatform() =>
        RuntimeInformation.ProcessArchitecture != Architecture.X64 ? ""
        : OperatingSystem.IsWindows() ? "win-x64" : OperatingSystem.IsLinux() ? "linux-x64" : "";

    public string ReadyDirectory => Path.Combine(_options.EffectiveUpdateDirectory, "ready");

    public static bool TryParseVersion(string? text, out (int, int, int) version)
    {
        version = default;
        if (string.IsNullOrWhiteSpace(text) || !VersionPattern.IsMatch(text.Trim())) return false;
        var parts = text.Trim().Split('.').Select(int.Parse).ToArray();
        version = (parts[0], parts[1], parts[2]);
        return true;
    }

    /// <summary>Verifies the envelope and returns the manifest, or throws with a stable code. Pure: no I/O.</summary>
    public static UpdateManifest VerifyManifest(JobEnvelope envelope, WorkerOptions options, string currentVersion, string platform,
        DateTimeOffset now, bool staged)
    {
        if (!JobVerifier.VerifySignature(envelope.Payload, envelope.Signature, options.SigningPublicKey))
            throw new UpdateRejectedException("invalid_signature");
        UpdateManifest? manifest;
        try { manifest = JsonSerializer.Deserialize<UpdateManifest>(envelope.Payload); }
        catch (JsonException) { throw new UpdateRejectedException("invalid_manifest"); }
        if (manifest is null || manifest.Type != "worker_update") throw new UpdateRejectedException("invalid_manifest");
        if (!Guid.TryParse(options.TenantId, out var tenant) || manifest.TenantId != tenant) throw new UpdateRejectedException("tenant_mismatch");
        if (!Guid.TryParse(options.WorkerId, out var worker) || manifest.WorkerId != worker) throw new UpdateRejectedException("worker_mismatch");
        if (manifest.Platform != platform || platform.Length == 0) throw new UpdateRejectedException("platform_mismatch");
        if (!TryParseVersion(manifest.Version, out var target)) throw new UpdateRejectedException("invalid_version");
        if (!HashPattern.IsMatch(manifest.Sha256)) throw new UpdateRejectedException("invalid_hash");
        if (manifest.SizeBytes is < 1 or > MaxPackageBytes) throw new UpdateRejectedException("invalid_size");
        // A staged package may wait for a restart; an offer must be fresh. Either way a replayed old manifest dies on its age.
        if (staged ? now - manifest.IssuedAt > StagedValidity : now > manifest.ExpiresAt) throw new UpdateRejectedException("manifest_expired");
        if (!TryParseVersion(currentVersion, out var current)) throw new UpdateRejectedException("unknown_current_version");
        if (manifest.Rollback ? target == current : target.CompareTo(current) <= 0) throw new UpdateRejectedException("not_an_update");
        return manifest;
    }

    public static string Sha256Hex(Stream stream)
    {
        using var sha = SHA256.Create();
        return Convert.ToHexString(sha.ComputeHash(stream)).ToLowerInvariant();
    }

    /// <summary>
    /// Re-verification for the apply script, run by the CURRENTLY installed (trusted) binary: signature, binding, hash and
    /// size of the staged package. Returns "ok" or a stable failure code.
    /// </summary>
    public static string VerifyStaged(WorkerOptions options, string readyDirectory, DateTimeOffset now, string currentVersion)
    {
        try
        {
            var pendingPath = Path.Combine(readyDirectory, "pending.json");
            var packagePath = Path.Combine(readyDirectory, "package.pkg");
            if (!File.Exists(pendingPath) || !File.Exists(packagePath)) return "nothing_staged";
            var pending = JsonSerializer.Deserialize<PendingUpdate>(File.ReadAllText(pendingPath));
            if (pending is null) return "invalid_pending";
            var manifest = VerifyManifest(new JobEnvelope { Payload = pending.Payload, Signature = pending.Signature }, options,
                currentVersion, CurrentPlatform(), now, staged: true);
            if (manifest.Version != pending.Version || manifest.Sha256 != pending.Sha256) return "pending_mismatch";
            var info = new FileInfo(packagePath);
            if (info.Length != manifest.SizeBytes) return "size_mismatch";
            using var stream = info.OpenRead();
            return Sha256Hex(stream) == manifest.Sha256 ? "ok" : "hash_mismatch";
        }
        catch (UpdateRejectedException rejected) { return rejected.Code; }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException) { return "unreadable"; }
    }

    /// <summary>One update attempt. True when a new package was staged (a restart will apply it).</summary>
    public async Task<bool> TryStageAsync(CancellationToken ct)
    {
        var platform = CurrentPlatform();
        if (platform.Length == 0) return false;
        var offer = await _api.CheckUpdateAsync(platform, ct);
        if (offer is null) return false;
        UpdateManifest manifest;
        try { manifest = VerifyManifest(offer, _options, WorkerOptions.Version, platform, _time.GetUtcNow(), staged: false); }
        catch (UpdateRejectedException rejected)
        {
            _logger.LogWarning("Update offer rejected: {Code}", rejected.Code);
            await TryReportAsync(null, offer, "FAILED", rejected.Code, ct);
            return false;
        }
        if (IsAlreadyStaged(manifest)) return false;

        var incoming = Path.Combine(_options.EffectiveUpdateDirectory, "incoming");
        Directory.CreateDirectory(incoming);
        var partial = Path.Combine(incoming, manifest.Version + ".part");
        try
        {
            await using (var file = new FileStream(partial, FileMode.Create, FileAccess.Write, FileShare.None))
                await _api.DownloadReleaseAsync(manifest.Version, platform, file, manifest.SizeBytes, ct);
            var info = new FileInfo(partial);
            if (info.Length != manifest.SizeBytes) throw new UpdateRejectedException("size_mismatch");
            using (var check = info.OpenRead())
                if (Sha256Hex(check) != manifest.Sha256) throw new UpdateRejectedException("hash_mismatch");
            Directory.CreateDirectory(ReadyDirectory);
            var pendingPath = Path.Combine(ReadyDirectory, "pending.json");
            if (File.Exists(pendingPath)) File.Delete(pendingPath);  // never leave a manifest next to a package it does not describe
            File.Move(partial, Path.Combine(ReadyDirectory, "package.pkg"), overwrite: true);
            File.WriteAllText(pendingPath, JsonSerializer.Serialize(new PendingUpdate
            {
                Version = manifest.Version, Platform = platform, Sha256 = manifest.Sha256, Payload = offer.Payload,
                Signature = offer.Signature, StagedAt = _time.GetUtcNow()
            }));
        }
        catch (Exception ex) when (ex is UpdateRejectedException or IOException or HttpRequestException or CentralApiException or UnauthorizedAccessException)
        {
            var code = ex is UpdateRejectedException r ? r.Code : ex.GetType().Name;
            _logger.LogWarning("Update {Version} not staged: {Code}", manifest.Version, code);
            TryDelete(partial);
            await TryReportAsync(manifest, offer, "FAILED", code, ct);
            return false;
        }
        await TryReportAsync(manifest, offer, "DOWNLOADED", "", ct);
        _logger.LogInformation("Update {Version} staged; it is applied on the next restart", manifest.Version);
        return true;
    }

    private bool IsAlreadyStaged(UpdateManifest manifest)
    {
        try
        {
            var pendingPath = Path.Combine(ReadyDirectory, "pending.json");
            if (!File.Exists(pendingPath)) return false;
            var pending = JsonSerializer.Deserialize<PendingUpdate>(File.ReadAllText(pendingPath));
            return pending is not null && pending.Version == manifest.Version && pending.Sha256 == manifest.Sha256;
        }
        catch (Exception ex) when (ex is JsonException or IOException) { return false; }
    }

    private async Task TryReportAsync(UpdateManifest? manifest, JobEnvelope offer, string status, string error, CancellationToken ct)
    {
        var version = manifest?.Version;
        if (version is null)
        {
            try { version = JsonSerializer.Deserialize<UpdateManifest>(offer.Payload)?.Version; } catch (JsonException) { }
        }
        if (version is null || !TryParseVersion(version, out _)) return;
        try { await _api.ReportUpdateAsync(version, status, error, ct); }
        catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException) { /* reported on the next attempt */ }
    }

    private static void TryDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); } catch (IOException) { }
    }
}
