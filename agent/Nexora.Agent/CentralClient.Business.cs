using System.Net.Http.Json;

namespace Nexora.Agent;

public sealed partial class CentralClient
{
    public async Task SendBusinessAsync(BusinessSample value, CancellationToken cancellationToken)
    {
        using var request = Authorized(HttpMethod.Post, "api/v1/agent/business-events", JsonContent.Create(value));
        using var response = await _http.SendAsync(request, cancellationToken);
        response.EnsureSuccessStatusCode();
    }
}
