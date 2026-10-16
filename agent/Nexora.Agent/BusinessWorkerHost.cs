using System.Net;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Nexora.BusinessWorker;

namespace Nexora.Agent;

/// <summary>
/// Business Worker embutido no agente de monitoramento. Autentica como o próprio agente (sem token de worker), usa só
/// as fontes configuradas e nunca derruba o agente: qualquer falha é registrada e tentada de novo mais tarde.
/// Desligado por padrão (AgentOptions.BusinessEnabled).
/// </summary>
public sealed class BusinessWorkerHost : BackgroundService
{
    private static readonly TimeSpan NotReadyRetry = TimeSpan.FromMinutes(5);
    private static readonly TimeSpan FailureRetry = TimeSpan.FromMinutes(1);
    private readonly AgentOptions _options;
    private readonly CentralClient _central;
    private readonly BusinessSecretStore _secrets;
    private readonly ILoggerFactory _logs;
    private readonly ILogger<BusinessWorkerHost> _logger;

    public BusinessWorkerHost(IOptions<AgentOptions> options, CentralClient central, BusinessSecretStore secrets, ILoggerFactory logs)
    {
        _options = options.Value; _central = central; _secrets = secrets; _logs = logs; _logger = logs.CreateLogger<BusinessWorkerHost>();
    }

    protected override async Task ExecuteAsync(CancellationToken stop)
    {
        if (!_options.BusinessEnabled) { BusinessLog.Write("Consultas de negócio desligadas neste agente (BusinessEnabled=false)."); return; }
        _logger.LogInformation("Consultas de negócio habilitadas neste agente.");
        BusinessLog.Write($"Consultas de negócio habilitadas; {_options.BusinessSources.Length} fonte(s) configurada(s).");
        while (!stop.IsCancellationRequested)
        {
            try
            {
                if (_central.Credentials is null) { BusinessLog.Write("Aguardando o cadastro do agente na Central."); await Task.Delay(TimeSpan.FromSeconds(15), stop); continue; }
                var identity = await FetchIdentityAsync(stop);
                if (identity is null) { BusinessLog.Write("A Central ainda não tem um worker deste agente (Negócios > Workers). Nova tentativa em 5 minutos."); await Task.Delay(NotReadyRetry, stop); continue; }
                await RunAsync(identity, stop);
            }
            catch (OperationCanceledException) when (stop.IsCancellationRequested) { break; }
            catch (Exception ex)
            {
                _logger.LogWarning("Consultas de negócio: falha ({Error}); nova tentativa em {Seconds}s.", ex.GetType().Name, (int)FailureRetry.TotalSeconds);
                BusinessLog.Write($"Falha: {ex.GetType().Name}: {ex.Message}. Nova tentativa em {(int)FailureRetry.TotalSeconds}s.");
                try { await Task.Delay(FailureRetry, stop); } catch (OperationCanceledException) { break; }
            }
        }
    }

    private sealed record Identity(string WorkerId, string PublicKey);

    private async Task<Identity?> FetchIdentityAsync(CancellationToken stop)
    {
        var credentials = _central.Credentials!;
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(new Uri(_options.ServerUrl.TrimEnd('/') + "/"), "api/v1/agents/business/identity"));
        request.Headers.Add("X-Tenant-ID", credentials.TenantId);
        request.Headers.Add("X-Agent-ID", credentials.AgentId);
        request.Headers.Add("X-Agent-Token", credentials.AgentToken);
        using var response = await http.SendAsync(request, stop);
        BusinessLog.Write($"Identidade do worker na Central: HTTP {(int)response.StatusCode}.");
        if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.NotFound)
        {
            _logger.LogInformation("Este agente ainda não tem um Business Worker habilitado na Central (Negócios > Workers).");
            return null;
        }
        response.EnsureSuccessStatusCode();
        var body = await response.Content.ReadFromJsonAsync<Dictionary<string, System.Text.Json.JsonElement>>(cancellationToken: stop);
        var workerId = body?.GetValueOrDefault("worker_id").GetString();
        var key = body?.GetValueOrDefault("public_key").GetString();
        return string.IsNullOrWhiteSpace(workerId) || string.IsNullOrWhiteSpace(key) ? null : new Identity(workerId, key);
    }

    private async Task RunAsync(Identity identity, CancellationToken stop)
    {
        var credentials = _central.Credentials!;
        var passwords = _options.BusinessSources.ToDictionary(s => s.Ref, s => _secrets.Load(s.Ref));
        var options = new WorkerOptions
        {
            ServerUrl = _options.ServerUrl, TenantId = credentials.TenantId, WorkerId = identity.WorkerId, SigningPublicKey = identity.PublicKey,
            PollSeconds = 30, HeartbeatSeconds = 30, UpdateCheckMinutes = 0,  // o agente se atualiza como hoje; o worker embutido nunca se atualiza sozinho
            AllowUntrustedServerCertificate = _options.BusinessSources.Any(s => s.TrustServerCertificate),
        };
        var connections = _options.BusinessSources.ToDictionary(
            s => $"NEXORA_BW_DB_{s.Ref.ToUpperInvariant()}", s => BusinessConnection.Build(s, passwords[s.Ref]));
        var secrets = new SecretResolver(options, name => connections.GetValueOrDefault(name));
        var api = CentralApi.ForAgent(new HttpClient(), _options.ServerUrl, credentials.TenantId, credentials.AgentId, credentials.AgentToken);
        var runner = new JobRunner(api, new DbQueryExecutor(options), secrets, options, _logs.CreateLogger<JobRunner>());
        var heartbeat = new HeartbeatService(api, options, _logs.CreateLogger<HeartbeatService>());
        var polling = new JobPollingService(api, runner, options, _logs.CreateLogger<JobPollingService>());
        _logger.LogInformation("Business Worker embutido em execução (worker {WorkerId}, {Count} fonte(s)).", identity.WorkerId, connections.Count);
        BusinessLog.Write($"Business Worker embutido em execução (worker {identity.WorkerId}, {connections.Count} fonte(s)).");
        await heartbeat.StartAsync(stop);
        await polling.StartAsync(stop);
        try
        {
            // Se qualquer um dos dois lacos morrer, volta ao laco externo, que reconstroi tudo depois da espera.
            var finished = await Task.WhenAny(heartbeat.ExecuteTask!, polling.ExecuteTask!, Task.Delay(Timeout.Infinite, stop));
            stop.ThrowIfCancellationRequested();
            if (finished.IsFaulted) throw new InvalidOperationException("worker_loop_faulted", finished.Exception);
            throw new InvalidOperationException("worker_loop_ended");
        }
        finally
        {
            await heartbeat.StopAsync(CancellationToken.None);
            await polling.StopAsync(CancellationToken.None);
        }
    }
}
