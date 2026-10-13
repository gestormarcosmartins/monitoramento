using Microsoft.Extensions.Options;

namespace Nexora.Agent;

public sealed class AgentWorker : BackgroundService
{
    private readonly CentralClient _central;
    private readonly LocalQueue _queue;
    private readonly AgentOptions _options;
    private readonly ILogger<AgentWorker> _logger;
    private readonly HostCollector _host;
    private readonly SqlServerCollector _sql;
#if CHAVEOU_AGENT
    private readonly ChaveouMonitorCollector _chaveou;
#endif
    private readonly HashSet<string> _processed = new(StringComparer.OrdinalIgnoreCase);

    public AgentWorker(CentralClient central, LocalQueue queue, IOptions<AgentOptions> options, HostCollector host, SqlServerCollector sql,
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
#if CHAVEOU_AGENT
        _chaveou = chaveou;
#endif
        _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        DateTimeOffset nextServices=DateTimeOffset.MinValue,nextHost=DateTimeOffset.MinValue,nextSql=DateTimeOffset.MinValue,nextPerformance=DateTimeOffset.MinValue;
#if CHAVEOU_AGENT
        DateTimeOffset nextChaveou=DateTimeOffset.MinValue,nextChaveouPerformance=DateTimeOffset.MinValue;
#endif
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await _central.EnrollAsync(stoppingToken);
                var config = await _central.ConfigurationAsync(stoppingToken);
                var now=DateTimeOffset.UtcNow;
                var health=new Dictionary<string,object?> { ["machine"]=Environment.MachineName,["heartbeat_at"]=now,["queue_depth"]=_queue.Files().Count() };
                if(now>=nextServices){health["services"]=_host.Services(_options.EssentialServices);nextServices=now.AddSeconds(Math.Max(30,_options.ServicesSeconds));}
                if(now>=nextHost){foreach(var pair in _host.Metrics())health[pair.Key]=pair.Value;var hostSnapshot=_host.Operational(config,_options.EssentialServices,_options.AgentName);if(hostSnapshot is not null)await _queue.EnqueueAsync(hostSnapshot,stoppingToken);nextHost=now.AddSeconds(Math.Max(60,_options.HostMetricsSeconds));}
                if(now>=nextSql){health["sql"]=await _sql.HealthAsync(stoppingToken);var snapshot=await _sql.OperationalAsync(config,stoppingToken);if(snapshot is not null)await _queue.EnqueueAsync(snapshot,stoppingToken);nextSql=now.AddSeconds(Math.Max(60,_options.SqlHealthSeconds));}
                if(now>=nextPerformance){var snapshot=await _sql.PerformanceAsync(config,stoppingToken);if(snapshot is not null)await _queue.EnqueueAsync(snapshot,stoppingToken);nextPerformance=now.AddSeconds(Math.Max(900,_options.SqlPerformanceSeconds));}
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
}
