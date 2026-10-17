using System.Net.Http.Json;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Nexora.BusinessWorker;

namespace Nexora.Agent;

public sealed class CentralClient
{
    // sql-server-direct is reported only when this install actually has SQL Server
    // configured: Central auto-provisions a set of SQL monitors for any agent that
    // declares this capability, and a blanket static capability would create those
    // monitors (permanently stuck at "unknown") on machines with no SQL Server at all.
#if CHAVEOU_AGENT
    private string[] AgentCapabilities => ["host-health", "chaveou-monitor", "postgresql-direct", "offline-queue"];
#else
    private string[] AgentCapabilities => _options.SqlEnabled
        ? ["host-health", "windows-services", "host-disk-levels", "sql-server-direct", "offline-queue"]
        : ["host-health", "windows-services", "host-disk-levels", "offline-queue"];
#endif
    private readonly HttpClient _http;
    private readonly AgentOptions _options;
    private readonly CredentialStore _store;
    public Credentials? Credentials { get; private set; }

    public CentralClient(HttpClient http, IOptions<AgentOptions> options, CredentialStore store)
    {
        _http = http;
        _options = options.Value;
        _store = store;
        Credentials = store.Load();
    }

    private Uri Uri(string path) => new(new Uri(_options.ServerUrl.TrimEnd('/') + "/"), path.TrimStart('/'));
    private HttpRequestMessage Authorized(HttpMethod method, string path, HttpContent? content = null)
    {
        if (Credentials is null) throw new InvalidOperationException("Agente ainda não cadastrado.");
        var request = new HttpRequestMessage(method, Uri(path)) { Content = content };
        request.Headers.Add("X-Tenant-ID", Credentials.TenantId);
        request.Headers.Add("X-Agent-ID", Credentials.AgentId);
        request.Headers.Add("X-Agent-Token", Credentials.AgentToken);
        return request;
    }

    public async Task EnrollAsync(CancellationToken cancellationToken)
    {
        if (Credentials is not null) return;
        if (string.IsNullOrWhiteSpace(_options.EnrollmentToken))
            throw new InvalidOperationException("Informe Agent:EnrollmentToken ou NEXORA_ENROLLMENT_TOKEN no primeiro uso.");
        var version = AgentVersion.Current;
        var response = await _http.PostAsJsonAsync(Uri("api/v1/agent/enroll"), new {
            enrollment_token = _options.EnrollmentToken,
            name = _options.AgentName,
            machine_id = $"{Environment.MachineName}:{Environment.OSVersion.VersionString}",
            version,
            capabilities = AgentCapabilities
        }, cancellationToken);
        response.EnsureSuccessStatusCode();
        Credentials = await response.Content.ReadFromJsonAsync<Credentials>(cancellationToken: cancellationToken)
            ?? throw new InvalidOperationException("A Central retornou credenciais inválidas.");
        _store.Save(Credentials);
    }

    public async Task HeartbeatAsync(Dictionary<string,object?> health, CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Post, "api/v1/agent/heartbeat",
            JsonContent.Create(new { version = AgentVersion.Current, capabilities = AgentCapabilities, heartbeat_interval_seconds = _options.HeartbeatSeconds, services_interval_seconds = _options.ServicesSeconds, health }));
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    /// <summary>Oferta de atualização assinada para este agente, ou null quando não há (anel hold, já na versão mais nova, etc.).</summary>
    public async Task<JobEnvelope?> CheckUpdateAsync(string platform, CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Get, $"api/v1/agent/update?platform={System.Uri.EscapeDataString(platform)}");
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        var offer = await response.Content.ReadFromJsonAsync<UpdateOffer>(cancellationToken: cancellationToken);
        return offer?.Update;
    }

    /// <summary>Baixa o pacote oferecido; recusa gravar mais do que o manifesto prometeu. Cliente próprio: o padrão tem limite de 30 s.</summary>
    public async Task DownloadUpdateAsync(string version, string platform, Stream destination, long maxBytes, CancellationToken cancellationToken)
    {
        using var download = new HttpClient { Timeout = TimeSpan.FromMinutes(15) };
        using var request = Authorized(HttpMethod.Get, $"api/v1/agent/releases/{System.Uri.EscapeDataString(version)}/{System.Uri.EscapeDataString(platform)}/download");
        using var response = await download.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
        response.EnsureSuccessStatusCode();
        await using var body = await response.Content.ReadAsStreamAsync(cancellationToken);
        var buffer = new byte[81920];
        long total = 0;
        int read;
        while ((read = await body.ReadAsync(buffer, cancellationToken)) > 0)
        {
            total += read;
            if (total > maxBytes) throw new UpdateRejectedException("size_mismatch");
            await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
        }
    }

    public async Task ReportUpdateAsync(string version, string status, string error, CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Post, "api/v1/agent/update/status",
            JsonContent.Create(new { version, status, error = error.Length > 200 ? error[..200] : error }));
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    public async Task<AgentConfiguration> ConfigurationAsync(CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Get, "api/v1/agent/config");
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<AgentConfiguration>(cancellationToken: cancellationToken)
            ?? new AgentConfiguration([], default);
    }

    public async Task SendAsync(SnapshotEnvelope snapshot, CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Post, "api/v1/agent/snapshots", JsonContent.Create(snapshot));
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }

    // Dedicated path for the Benner health-monitor port (see Benner/BennerSqlServerDiagnostics.cs
    // and AgentWorker's Benner cycle): this endpoint is authenticated with a shared ingest token
    // + tenant id (X-Tenant-ID / X-Agent-Token), NOT this agent's enrollment Credentials, so it
    // deliberately does not go through Authorized()/the usual EnsureSuccessStatusCode() pattern -
    // the caller decides what to do with a false result (e.g. spool to disk and retry later).
    // Accepts pre-serialized JSON so a spooled/retried payload can be replayed without a
    // deserialize-then-reserialize round trip.
    public async Task<bool> SendBennerSnapshotJsonAsync(string payloadJson, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(_options.BennerTenantId) || string.IsNullOrWhiteSpace(_options.BennerIngestToken))
            return false; // Not configured: mirrors Python's "desativado" skip (local archive still written, nothing sent).
        using var request = new HttpRequestMessage(HttpMethod.Post, Uri("api/v1/agents/benner/snapshots?activate_alerts=true"))
        {
            Content = new StringContent(payloadJson, System.Text.Encoding.UTF8, "application/json"),
        };
        request.Headers.Add("X-Tenant-ID", _options.BennerTenantId);
        request.Headers.Add("X-Agent-Token", _options.BennerIngestToken);
        using var response = await _http.SendAsync(request, cancellationToken);
        return response.IsSuccessStatusCode;
    }

    public Task<bool> SendBennerSnapshotAsync(Benner.BennerSnapshotPayload payload, CancellationToken cancellationToken) =>
        SendBennerSnapshotJsonAsync(JsonSerializer.Serialize(payload), cancellationToken);
}
