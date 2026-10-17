using Microsoft.Extensions.Options;
using Nexora.Agent;
using Nexora.Agent.Benner;
using Nexora.BusinessWorker;

// Comandos usados pelo script de aplicação da atualização (rodam e saem, sem subir o agente):
if (args.Length >= 1 && args[0] == "--version") { Console.WriteLine(AgentVersion.Current); return 0; }
if (args.Length >= 2 && args[0] == "--verify-staged") return VerifyStagedCommand(args[1]);

var builder = Host.CreateApplicationBuilder(args);
// Nexora.Agent.Config writes the settings a human edits to agentsettings.json next to
// the executable; CreateApplicationBuilder only auto-loads appsettings.json, so without
// this the service never saw EnrollmentToken/credentials the configurator just saved.
builder.Configuration.AddJsonFile(
    Path.Combine(AppContext.BaseDirectory, "agentsettings.json"), optional: true, reloadOnChange: false);
var environmentOverrides = new Dictionary<string, string?>
{
    ["Agent:ServerUrl"] = Environment.GetEnvironmentVariable("NEXORA_SERVER_URL"),
    ["Agent:TenantId"] = Environment.GetEnvironmentVariable("NEXORA_TENANT_ID"),
    ["Agent:EnrollmentToken"] = Environment.GetEnvironmentVariable("NEXORA_ENROLLMENT_TOKEN"),
    ["Agent:AgentName"] = Environment.GetEnvironmentVariable("NEXORA_AGENT_NAME"),
    ["Agent:SnapshotDirectory"] = Environment.GetEnvironmentVariable("NEXORA_SNAPSHOT_DIRECTORY"),
    ["Agent:DataDirectory"] = Environment.GetEnvironmentVariable("NEXORA_DATA_DIRECTORY"),
    ["Agent:ChaveouConnectionString"] = Environment.GetEnvironmentVariable("NEXORA_CHAVEOU_DATABASE_URL"),
    ["Agent:ChaveouTenantPublicId"] = Environment.GetEnvironmentVariable("NEXORA_CHAVEOU_TENANT_ID"),
};
var essentialServices = Environment.GetEnvironmentVariable("NEXORA_ESSENTIAL_SERVICES")?
    .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
if (essentialServices is not null)
    for (var index = 0; index < essentialServices.Length; index++)
        environmentOverrides[$"Agent:EssentialServices:{index}"] = essentialServices[index];
builder.Configuration.AddInMemoryCollection(environmentOverrides.Where(x => !string.IsNullOrWhiteSpace(x.Value))!);
if (OperatingSystem.IsWindows())
    builder.Services.AddWindowsService(options => options.ServiceName = "Nexora Monitoring Agent");
else
    builder.Services.AddSystemd();
builder.Services.Configure<AgentOptions>(builder.Configuration.GetSection("Agent"));
builder.Services.AddSingleton(new HttpClient { Timeout = TimeSpan.FromSeconds(30) });
builder.Services.AddSingleton<CentralClient>();
builder.Services.AddSingleton<CredentialStore>();
builder.Services.AddSingleton<SqlCredentialStore>();
builder.Services.AddSingleton<HostCollector>();
builder.Services.AddSingleton<SqlServerCollector>();
builder.Services.AddSingleton<IBennerEngineDiagnostics, BennerSqlServerDiagnostics>();
builder.Services.AddSingleton<LocalQueue>();
#if CHAVEOU_AGENT
builder.Services.AddSingleton<ChaveouMonitorCollector>();
#endif
builder.Services.AddSingleton<BusinessSecretStore>();
builder.Services.AddSingleton<UpdateGate>();
builder.Services.AddHostedService<AgentUpdateService>();  // só age no Windows e com UpdateCheckMinutes > 0; o anel padrão (hold) não recebe nada
builder.Services.AddHostedService<BusinessWorkerHost>();  // sai na hora se AgentOptions.BusinessEnabled for false
builder.Services.AddHostedService<AgentWorker>();
await builder.Build().RunAsync();
return 0;

// "ok" ou um código estável: assinatura, vínculo a esta empresa/agente, hash e tamanho do pacote preparado.
static int VerifyStagedCommand(string updatesDirectory)
{
    var configuration = new ConfigurationBuilder().AddJsonFile(Path.Combine(AppContext.BaseDirectory, "agentsettings.json"), optional: true).Build();
    var options = configuration.GetSection("Agent").Get<AgentOptions>() ?? new AgentOptions();
    var credentials = new CredentialStore(Options.Create(options)).Load();
    var verdict = credentials is null ? "no_credentials" : AgentUpdateVerifier.VerifyStaged(Path.Combine(updatesDirectory, "ready"), options.UpdateSigningPublicKey,
        Guid.Parse(credentials.TenantId), Guid.Parse(credentials.AgentId), AgentVersion.Current, UpdateStager.CurrentPlatform(), DateTimeOffset.UtcNow);
    Console.WriteLine(verdict);
    return verdict == "ok" ? 0 : 3;
}
