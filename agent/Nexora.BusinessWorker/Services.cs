using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Nexora.BusinessWorker;

/// <summary>Heartbeat of the business process. Independent of the job loop: a stuck query never silences it.</summary>
public sealed class HeartbeatService : BackgroundService
{
    private readonly ICentralApi _api;
    private readonly WorkerOptions _options;
    private readonly ILogger<HeartbeatService> _logger;
    private readonly UpdateGate? _gate;

    public HeartbeatService(ICentralApi api, WorkerOptions options, ILogger<HeartbeatService> logger, UpdateGate? gate = null)
    {
        _api = api; _options = options; _logger = logger; _gate = gate;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await _api.HeartbeatAsync(stoppingToken); _gate?.MarkHealthy(); }
            catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException && !stoppingToken.IsCancellationRequested)
            {
                _logger.LogWarning("Heartbeat failed: {Error}", ex.GetType().Name);
            }
            await Task.Delay(TimeSpan.FromSeconds(_options.HeartbeatSeconds), stoppingToken).ConfigureAwait(false);
        }
    }
}

/// <summary>Claim loop. Without API access it simply stops running queries and keeps retrying (spec section 4).</summary>
public sealed class JobPollingService : BackgroundService
{
    private readonly ICentralApi _api;
    private readonly JobRunner _runner;
    private readonly WorkerOptions _options;
    private readonly ILogger<JobPollingService> _logger;
    private readonly UpdateGate? _gate;

    public JobPollingService(ICentralApi api, JobRunner runner, WorkerOptions options, ILogger<JobPollingService> logger, UpdateGate? gate = null)
    {
        _api = api; _runner = runner; _options = options; _logger = logger; _gate = gate;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var failures = 0;
        while (!stoppingToken.IsCancellationRequested)
        {
            var delay = TimeSpan.FromSeconds(_options.PollSeconds);
            if (_gate is { RestartPending: true }) { await Task.Delay(TimeSpan.FromSeconds(2), stoppingToken).ConfigureAwait(false); continue; }  // an update waits for the current job
            try
            {
                var jobs = await _api.ClaimAsync(1, stoppingToken);  // concurrency is 1 per worker
                failures = 0;
                foreach (var job in jobs) await _runner.RunAsync(job, stoppingToken);
                if (jobs.Count > 0) delay = TimeSpan.FromSeconds(1);  // more work may be waiting
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException)
            {
                failures++;
                delay = TimeSpan.FromSeconds(Math.Min(60, _options.PollSeconds * Math.Pow(2, Math.Min(failures, 4))));
                _logger.LogWarning("Claim failed ({Error}); no queries will run until the API answers. Retrying in {Delay}s",
                    ex.GetType().Name, (int)delay.TotalSeconds);
            }
            await Task.Delay(delay, stoppingToken).ConfigureAwait(false);
        }
    }
}
