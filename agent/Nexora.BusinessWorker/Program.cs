using System.Diagnostics;
using Nexora.BusinessWorker;

var builder = Host.CreateApplicationBuilder(args);
var options = builder.Configuration.GetSection("Nexora").Get<WorkerOptions>() ?? new WorkerOptions();
// Convenience overrides for container/systemd deployments; secrets still come from the environment or files.
options.ServerUrl = Environment.GetEnvironmentVariable("NEXORA_BW_SERVER_URL") ?? options.ServerUrl;
options.TenantId = Environment.GetEnvironmentVariable("NEXORA_BW_TENANT_ID") ?? options.TenantId;
options.WorkerId = Environment.GetEnvironmentVariable("NEXORA_BW_WORKER_ID") ?? options.WorkerId;
options.SigningPublicKey = Environment.GetEnvironmentVariable("NEXORA_BW_SIGNING_KEY") ?? options.SigningPublicKey;
if (Environment.GetEnvironmentVariable("NEXORA_BW_POLL_SECONDS") is { } poll && int.TryParse(poll, out var pollSeconds)) options.PollSeconds = pollSeconds;
if (Environment.GetEnvironmentVariable("NEXORA_BW_ALLOW_PLAINTEXT_LOOPBACK_DB") == "1") options.AllowPlaintextLoopbackDatabase = true;

var problems = options.Validate();
var secrets = new SecretResolver(options);
var token = secrets.WorkerToken();
if (string.IsNullOrWhiteSpace(token)) problems = problems.Append("Worker token missing: set NEXORA_BW_TOKEN or Nexora:TokenFile.").ToList();
if (problems.Count > 0)
{
    // Fail fast and loud: a half-configured worker must not poll the central.
    foreach (var problem in problems) Console.Error.WriteLine($"Configuration error: {problem}");
    return 2;
}

// Below-normal priority keeps the infrastructure agent ahead of the business process under CPU pressure.
try { Process.GetCurrentProcess().PriorityClass = ProcessPriorityClass.BelowNormal; } catch (Exception) { /* not permitted: ignore */ }

if (OperatingSystem.IsWindows())
    builder.Services.AddWindowsService(o => o.ServiceName = "Nexora Business Worker");
else
    builder.Services.AddSystemd();
builder.Services.AddSingleton(options);
builder.Services.AddSingleton(secrets);
builder.Services.AddSingleton<ICentralApi>(_ => new CentralApi(new HttpClient(), options, token!));
builder.Services.AddSingleton<IQueryExecutor, DbQueryExecutor>();
builder.Services.AddSingleton<JobRunner>();
builder.Services.AddHostedService<HeartbeatService>();
builder.Services.AddHostedService<JobPollingService>();
await builder.Build().RunAsync();
return 0;
