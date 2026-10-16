using System.Net;
using Nexora.Agent;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Business Worker embutido no agente: autenticação pela identidade do agente e montagem segura da conexão.</summary>
public class AgentEmbeddingTests
{
    private sealed class Capture : HttpMessageHandler
    {
        public HttpRequestMessage? Last { get; private set; }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Last = request;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") });
        }
    }

    [Fact]
    public async Task Agent_mode_sends_the_agent_identity_and_no_worker_credentials()
    {
        var capture = new Capture();
        var api = CentralApi.ForAgent(new HttpClient(capture), "https://nexora.example/", "tenant-1", "agent-1", "agent-secret");
        await api.HeartbeatAsync(CancellationToken.None);
        var headers = capture.Last!.Headers;
        Assert.Equal("tenant-1", headers.GetValues("X-Tenant-ID").Single());
        Assert.Equal("agent-1", headers.GetValues("X-Agent-ID").Single());
        Assert.Equal("agent-secret", headers.GetValues("X-Agent-Token").Single());
        Assert.False(headers.Contains("X-Worker-ID"));
        Assert.False(headers.Contains("X-Worker-Token"));
        Assert.Equal("https://nexora.example/api/v1/agents/business/heartbeat", capture.Last.RequestUri!.ToString());
    }

    [Fact]
    public async Task Standalone_mode_still_sends_the_worker_credentials()
    {
        var capture = new Capture();
        var options = new WorkerOptions { ServerUrl = "https://nexora.example", TenantId = "tenant-1", WorkerId = "worker-1" };
        await new CentralApi(new HttpClient(capture), options, "worker-secret").HeartbeatAsync(CancellationToken.None);
        var headers = capture.Last!.Headers;
        Assert.Equal("worker-1", headers.GetValues("X-Worker-ID").Single());
        Assert.Equal("worker-secret", headers.GetValues("X-Worker-Token").Single());
        Assert.False(headers.Contains("X-Agent-ID"));
    }

    private static BusinessSource Source(string server = "srv\\SQLEXPRESS", string database = "JRI_HUB", string user = "nexora_ro") =>
        new() { Ref = "jri_hub", Server = server, Database = database, User = user };

    [Fact]
    public void Connection_string_is_built_from_the_source_and_password()
    {
        Assert.Equal("Server=srv\\SQLEXPRESS;Database=JRI_HUB;User Id=nexora_ro;Password=s3cret;", BusinessConnection.Build(Source(), "s3cret"));
    }

    [Theory]
    [InlineData("srv;TrustServerCertificate=true", "JRI_HUB", "u", "p")]
    [InlineData("srv", "db;ApplicationIntent=ReadWrite", "u", "p")]
    [InlineData("srv", "db", "u;Password=x", "p")]
    [InlineData("srv", "db", "u", "p;Encrypt=false")]
    [InlineData("srv", "db", "u", "p\nq")]
    public void Injection_through_connection_fields_is_refused(string server, string database, string user, string password)
    {
        Assert.Throws<ArgumentException>(() => BusinessConnection.Build(Source(server, database, user), password));
    }

    [Fact]
    public void Only_sql_server_is_accepted_in_the_agent()
    {
        var source = Source(); source.Dialect = "postgres";
        Assert.Throws<ArgumentException>(() => BusinessConnection.Build(source, "p"));
    }

    [Theory]
    [InlineData("../evil")]
    [InlineData("a/b")]
    [InlineData("Jri")]
    [InlineData("")]
    public void Secret_file_names_cannot_escape_the_secrets_directory(string name)
    {
        Assert.Throws<ArgumentException>(() => BusinessSecretStore.FileFor(name));
    }

    [Fact]
    public void Secret_file_lives_under_the_agent_business_directory()
    {
        var file = BusinessSecretStore.FileFor("jri_hub");
        Assert.StartsWith(BusinessSecretStore.Directory, file);
        Assert.EndsWith("jri_hub.dat", file);
    }

    [Fact]
    public void Business_is_off_by_default_so_existing_agents_are_unchanged()
    {
        var options = new AgentOptions();
        Assert.False(options.BusinessEnabled);
        Assert.Empty(options.BusinessSources);
    }
}
