using Microsoft.Extensions.Options;
using Nexora.Agent.Benner;
using Nexora.BusinessWorker;

namespace Nexora.Agent;

public sealed class AgentWorker : BackgroundService
{
    private readonly CentralClient _central;
    private readonly LocalQueue _queue;
    private readonly AgentOptions _options;
    private readonly ILogger<AgentWorker> _logger;
    private readonly HostCollector _host;
    private readonly SqlServerCollector _sql;
    private readonly IBennerEngineDiagnostics _benner;
    private readonly UpdateGate _gate;
#if CHAVEOU_AGENT
    private readonly ChaveouMonitorCollector _chaveou;
#endif
    private readonly HashSet<string> _processed = new(StringComparer.OrdinalIgnoreCase);

    public AgentWorker(CentralClient central, LocalQueue queue, IOptions<AgentOptions> options, HostCollector host, SqlServerCollector sql,
        IBennerEngineDiagnostics benner, UpdateGate gate,
#if CHAVEOU_AGENT
        ChaveouMonitorCollector chaveou,
#endif
        ILogger<AgentWorker> logger)
    {
        _central = central;
        _queue = queue;
        _options = options.Value;
        _host = host;
        _sql = sql;
        _benner = benner;
        _gate = gate;
#if CHAVEOU_AGENT
        _chaveou = chaveou;
#endif
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        DateTimeOffset nextServices=DateTimeOffset.MinValue,nextHost=DateTimeOffset.MinValue,nextSql=DateTimeOffset.MinValue,nextPerformance=DateTimeOffset.MinValue,nextBenner=DateTimeOffset.MinValue;
#if CHAVEOU_AGENT
        DateTimeOffset nextChaveou=DateTimeOffset.MinValue,nextChaveouPerformance=DateTimeOffset.MinValue;
#endif
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await _central.EnrollAsync(stoppingToken);
                var config = await CachedConfigurationAsync(stoppingToken);
                var now=DateTimeOffset.UtcNow;
                var health=new Dictionary<string,object?> { ["machine"]=Environment.MachineName,["heartbeat_at"]=now,["queue_depth"]=_queue.Files().Count() };
                if(now>=nextServices){health["services"]=_host.Services(_options.EssentialServices);nextServices=now.AddSeconds(Math.Max(30,_options.ServicesSeconds));}
                if(now>=nextHost){foreach(var pair in _host.Metrics())health[pair.Key]=pair.Value;var hostSnapshot=_host.Operational(config,_options.EssentialServices,_options.AgentName);if(hostSnapshot is not null)await _queue.EnqueueAsync(hostSnapshot,stoppingToken);nextHost=now.AddSeconds(Math.Max(60,_options.HostMetricsSeconds));}
                if(now>=nextSql){health["sql"]=await _sql.HealthAsync(stoppingToken);var snapshot=await _sql.OperationalAsync(config,stoppingToken);if(snapshot is not null)await _queue.EnqueueAsync(snapshot,stoppingToken);nextSql=now.AddSeconds(Math.Max(60,_options.SqlHealthSeconds));}
                if(now>=nextPerformance){var snapshot=await _sql.PerformanceAsync(config,stoppingToken);if(snapshot is not null)await _queue.EnqueueAsync(snapshot,stoppingToken);nextPerformance=now.AddSeconds(Math.Max(900,_options.SqlPerformanceSeconds));}
                if(_options.BennerEnabled && now>=nextBenner){await BennerCycleAsync(stoppingToken);nextBenner=now.AddSeconds(Math.Max(300,_options.BennerIntervalSeconds));}
#if CHAVEOU_AGENT
                if(now>=nextChaveou)
                {
                    health["postgresql"] = await _chaveou.HealthAsync(stoppingToken);
                    var chaveouSnapshot = await _chaveou.CollectAsync(config, stoppingToken);
                    if (chaveouSnapshot is not null) await _queue.EnqueueAsync(chaveouSnapshot, stoppingToken);
                    var postgresSnapshot = await _chaveou.OperationalAsync(config, stoppingToken);
                    if (postgresSnapshot is not null) await _queue.EnqueueAsync(postgresSnapshot, stoppingToken);
                    nextChaveou=now.AddSeconds(Math.Max(30,_options.ChaveouOperationalSeconds));
                }
                if(now>=nextChaveouPerformance)
                {
                    var performanceSnapshot = await _chaveou.PerformanceAsync(config, stoppingToken);
                    if (performanceSnapshot is not null) await _queue.EnqueueAsync(performanceSnapshot, stoppingToken);
                    nextChaveouPerformance=now.AddSeconds(Math.Max(900,_options.ChaveouPerformanceSeconds));
                }
#endif
                await _central.HeartbeatAsync(health,stoppingToken);
                _gate.MarkHealthy();  // período de teste de uma atualização: um heartbeat aceito pela Central prova que esta versão funciona
                await CollectAsync(config, stoppingToken);
                await FlushAsync(stoppingToken);
            }
            catch (Exception error)
            {
                _logger.LogError(error, "Falha no ciclo do agente; os dados permanecem na fila local.");
            }
            await Task.Delay(TimeSpan.FromSeconds(Math.Max(10, _options.HeartbeatSeconds)), stoppingToken);
        }
    }

    private async Task<AgentConfiguration> CachedConfigurationAsync(CancellationToken token)
    {
        var path=Path.Combine(AgentPaths.DataDirectory(_options), "last-agent-config.json");
        try
        {
            var config=await _central.ConfigurationAsync(token);
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            var tmp=path+".tmp";
            await File.WriteAllTextAsync(tmp,System.Text.Json.JsonSerializer.Serialize(config),token);
            File.Move(tmp,path,true);
            return config;
        }
        catch (Exception ex) when (!token.IsCancellationRequested)
        {
            _logger.LogWarning("Using last approved cached agent configuration: {ErrorType}",ex.GetType().Name);
            if (!File.Exists(path)) throw;
            var cached=System.Text.Json.JsonSerializer.Deserialize<AgentConfiguration>(await File.ReadAllTextAsync(path,token));
            return cached ?? throw new InvalidDataException("No valid cached configuration");
        }
    }

    private async Task CollectAsync(AgentConfiguration config, CancellationToken cancellationToken)
    {
        if (!Directory.Exists(_options.SnapshotDirectory)) return;
        foreach (var file in Directory.EnumerateFiles(_options.SnapshotDirectory, "*.json").OrderBy(x => x).TakeLast(100))
        {
            var signature = $"{file}|{File.GetLastWriteTimeUtc(file).Ticks}|{new FileInfo(file).Length}";
            if (!_processed.Add(signature)) continue;
            try
            {
                var snapshot = await BennerSnapshotReader.ReadAsync(file, config, cancellationToken);
                if (snapshot is not null) await _queue.EnqueueAsync(snapshot, cancellationToken);
            }
            catch (Exception error) { _logger.LogWarning(error, "Não foi possível processar {File}.", file); }
        }
    }

    private async Task FlushAsync(CancellationToken cancellationToken)
    {
        foreach (var file in _queue.Files())
        {
            var snapshot = await _queue.ReadAsync(file, cancellationToken);
            if (snapshot is null) { _queue.Complete(file); continue; }
            await _central.SendAsync(snapshot, cancellationToken);
            _queue.Complete(file);
        }
    }

    // The Benner health-monitor push (see Benner/BennerSqlServerDiagnostics.cs) deliberately
    // bypasses LocalQueue/SnapshotEnvelope/the monitor-matching pipeline above: it has its own
    // auth scheme (shared ingest token + tenant id, not this agent's enrollment credentials),
    // its own endpoint, and a payload shape Nexora's /agents/benner/snapshots consumes directly.
    // A failed send is spooled to its own small disk queue (BennerPendingDirectory) so a network
    // outage doesn't lose a collection - mirrors the Python collector's "local archive survives
    // network failure" behavior - without disturbing LocalQueue's own retry bookkeeping.
    private string BennerPendingDirectory => Path.Combine(AgentPaths.DataDirectory(_options), "benner", "pending");

    private async Task BennerCycleAsync(CancellationToken cancellationToken)
    {
        try
        {
            var payload = await _benner.CollectAsync(cancellationToken);
            if (payload is not null)
            {
                var json = System.Text.Json.JsonSerializer.Serialize(payload);
                if (!await _central.SendBennerSnapshotJsonAsync(json, cancellationToken))
                {
                    await BennerSpoolAsync(json, cancellationToken);
                    _logger.LogWarning("Não foi possível enviar o diagnóstico Benner (execution_id={ExecutionId}); mantido na fila local.", payload.ExecutionId);
                }
            }
        }
        catch (Exception error)
        {
            _logger.LogError(error, "Falha no ciclo de diagnóstico Benner.");
        }
        await BennerFlushPendingAsync(cancellationToken);
    }

    private async Task BennerSpoolAsync(string payloadJson, CancellationToken cancellationToken)
    {
        var directory = BennerPendingDirectory;
        Directory.CreateDirectory(directory);
        var final = Path.Combine(directory, $"{DateTimeOffset.UtcNow:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}.json");
        var temporary = final + ".tmp";
        await File.WriteAllTextAsync(temporary, payloadJson, cancellationToken);
        File.Move(temporary, final);
    }

    private async Task BennerFlushPendingAsync(CancellationToken cancellationToken)
    {
        var directory = BennerPendingDirectory;
        if (!Directory.Exists(directory)) return;
        foreach (var file in Directory.EnumerateFiles(directory, "*.json").OrderBy(x => x))
        {
            try
            {
                var json = await File.ReadAllTextAsync(file, cancellationToken);
                if (await _central.SendBennerSnapshotJsonAsync(json, cancellationToken)) File.Delete(file);
                else break; // Still unreachable/misconfigured: stop and retry the whole batch next cycle.
            }
            catch (Exception error)
            {
                _logger.LogWarning(error, "Falha ao reenviar diagnóstico Benner pendente {File}.", file);
                break;
            }
        }
    }
}
