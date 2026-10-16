using System.Net;
using System.Net.Http.Json;

namespace Nexora.BusinessWorker;

public sealed class CentralApiException : Exception
{
    public CentralApiException(HttpStatusCode status) : base($"central_api_{(int)status}") { Status = status; }
    public HttpStatusCode Status { get; }
}

/// <summary>The API is the Business Worker's only channel to the central: no broker, no central database.</summary>
public interface ICentralApi
{
    Task HeartbeatAsync(CancellationToken ct);
    Task<IReadOnlyList<JobEnvelope>> ClaimAsync(int limit, CancellationToken ct);
    /// <summary>False when the lease is no longer valid (another worker took the job or it expired).</summary>
    Task<bool> StartedAsync(Guid executionId, CancellationToken ct);
    Task<bool> RenewAsync(Guid executionId, CancellationToken ct);
    /// <summary>Returns the central verdict: accepted, duplicate or rejected.</summary>
    Task<string> ResultAsync(Guid executionId, ResultBody body, CancellationToken ct);
    Task FailedAsync(Guid executionId, FailureBody body, CancellationToken ct);
    /// <summary>Signed update offer for this worker, or null when it is up to date, on hold or not eligible.</summary>
    Task<JobEnvelope?> CheckUpdateAsync(string platform, CancellationToken ct);
    /// <summary>Streams the offered package; refuses to write more than <paramref name="maxBytes"/>.</summary>
    Task DownloadReleaseAsync(string version, string platform, Stream destination, long maxBytes, CancellationToken ct);
    Task ReportUpdateAsync(string version, string status, string error, CancellationToken ct);
}

public sealed class CentralApi : ICentralApi
{
    private readonly HttpClient _http;
    private readonly Action<HttpRequestMessage> _authenticate;

    public CentralApi(HttpClient http, WorkerOptions options, string token)
        : this(http, options.ServerUrl, request =>
        {
            request.Headers.Add("X-Tenant-ID", options.TenantId);
            request.Headers.Add("X-Worker-ID", options.WorkerId);
            request.Headers.Add("X-Worker-Token", token);
        }) { }

    /// <summary>Embedded mode: the worker runs inside the monitoring agent and authenticates as that agent (no worker token).</summary>
    public static CentralApi ForAgent(HttpClient http, string serverUrl, string tenantId, string agentId, string agentToken) =>
        new(http, serverUrl, request =>
        {
            request.Headers.Add("X-Tenant-ID", tenantId);
            request.Headers.Add("X-Agent-ID", agentId);
            request.Headers.Add("X-Agent-Token", agentToken);
        });

    private CentralApi(HttpClient http, string serverUrl, Action<HttpRequestMessage> authenticate)
    {
        _http = http;
        _authenticate = authenticate;
        _http.BaseAddress = new Uri(serverUrl.TrimEnd('/') + "/");
        _http.Timeout = TimeSpan.FromSeconds(30);
    }

    private HttpRequestMessage Request(HttpMethod method, string path, object? body = null)
    {
        var request = new HttpRequestMessage(method, path) { Content = body is null ? null : JsonContent.Create(body, body.GetType()) };
        _authenticate(request);
        return request;
    }

    private async Task<HttpResponseMessage> SendAsync(HttpMethod method, string path, object? body, CancellationToken ct, params HttpStatusCode[] tolerated)
    {
        var response = await _http.SendAsync(Request(method, path, body), ct);
        if (response.IsSuccessStatusCode || tolerated.Contains(response.StatusCode)) return response;
        var status = response.StatusCode;
        response.Dispose();
        throw new CentralApiException(status);
    }

    public async Task HeartbeatAsync(CancellationToken ct)
    {
        using var _ = await SendAsync(HttpMethod.Post, "api/v1/agents/business/heartbeat", new { version = WorkerOptions.Version }, ct);
    }

    public async Task<IReadOnlyList<JobEnvelope>> ClaimAsync(int limit, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Get, $"api/v1/agents/business/jobs/claim?limit={limit}", null, ct);
        var claim = await response.Content.ReadFromJsonAsync<ClaimResponse>(cancellationToken: ct);
        return claim?.Jobs ?? new List<JobEnvelope>();
    }

    public async Task<bool> StartedAsync(Guid executionId, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Post, $"api/v1/agents/business/jobs/{executionId}/started", new { }, ct, HttpStatusCode.Conflict, HttpStatusCode.NotFound);
        return response.IsSuccessStatusCode;
    }

    public async Task<bool> RenewAsync(Guid executionId, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Post, $"api/v1/agents/business/jobs/{executionId}/renew", new { }, ct, HttpStatusCode.Conflict, HttpStatusCode.NotFound);
        return response.IsSuccessStatusCode;
    }

    public async Task<string> ResultAsync(Guid executionId, ResultBody body, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Post, $"api/v1/agents/business/jobs/{executionId}/result", body, ct, HttpStatusCode.Conflict);
        if (!response.IsSuccessStatusCode) return "conflict";
        var verdict = await response.Content.ReadFromJsonAsync<Dictionary<string, object?>>(cancellationToken: ct);
        return verdict is not null && verdict.TryGetValue("status", out var status) ? status?.ToString() ?? "unknown" : "unknown";
    }

    public async Task FailedAsync(Guid executionId, FailureBody body, CancellationToken ct)
    {
        using var _ = await SendAsync(HttpMethod.Post, $"api/v1/agents/business/jobs/{executionId}/failed", body, ct);
    }

    public async Task<JobEnvelope?> CheckUpdateAsync(string platform, CancellationToken ct)
    {
        using var response = await SendAsync(HttpMethod.Get, $"api/v1/agents/business/update?platform={Uri.EscapeDataString(platform)}", null, ct);
        var offer = await response.Content.ReadFromJsonAsync<UpdateOffer>(cancellationToken: ct);
        return offer?.Update;
    }

    public async Task DownloadReleaseAsync(string version, string platform, Stream destination, long maxBytes, CancellationToken ct)
    {
        using var request = Request(HttpMethod.Get, $"api/v1/agents/business/releases/{Uri.EscapeDataString(version)}/{Uri.EscapeDataString(platform)}/download");
        using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
        if (!response.IsSuccessStatusCode) throw new CentralApiException(response.StatusCode);
        await using var body = await response.Content.ReadAsStreamAsync(ct);
        var buffer = new byte[81920];
        long total = 0;
        int read;
        while ((read = await body.ReadAsync(buffer, ct)) > 0)
        {
            total += read;
            if (total > maxBytes) throw new UpdateRejectedException("size_mismatch");  // never write more than the manifest promised
            await destination.WriteAsync(buffer.AsMemory(0, read), ct);
        }
    }

    public async Task ReportUpdateAsync(string version, string status, string error, CancellationToken ct)
    {
        using var _ = await SendAsync(HttpMethod.Post, "api/v1/agents/business/update/status",
            new { version, status, error = error.Length > 200 ? error[..200] : error }, ct);
    }
}
