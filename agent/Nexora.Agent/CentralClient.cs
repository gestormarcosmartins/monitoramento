using System.Net.Http.Json;
using System.Reflection;
using Microsoft.Extensions.Options;

namespace Nexora.Agent;

public sealed class CentralClient
{
#if CHAVEOU_AGENT
    private static readonly string[] AgentCapabilities = ["host-health", "chaveou-monitor", "postgresql-direct", "offline-queue"];
#else
    private static readonly string[] AgentCapabilities = ["host-health", "windows-services", "sql-server-direct", "offline-queue"];
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
        var version = Assembly.GetExecutingAssembly().GetName().Version?.ToString();
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
            JsonContent.Create(new { version = "2.1.0", capabilities = AgentCapabilities, heartbeat_interval_seconds = _options.HeartbeatSeconds, services_interval_seconds = _options.ServicesSeconds, health }));
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
}
