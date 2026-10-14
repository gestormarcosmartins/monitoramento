using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Nexora.BusinessWorker;

/// <summary>Shared flags between the loops: a pending restart stops new claims; a good heartbeat proves the running version works.</summary>
public sealed class UpdateGate
{
    private volatile bool _restartPending;
    private long _healthyTicks;
    public bool RestartPending => _restartPending;
    public void RequestRestart() => _restartPending = true;
    public bool Healthy => Interlocked.Read(ref _healthyTicks) != 0;
    public void MarkHealthy() => Interlocked.Exchange(ref _healthyTicks, DateTime.UtcNow.Ticks);
}

/// <summary>What the apply script leaves behind so the worker can tell the central how the last update ended.</summary>
public sealed class ApplyOutcome
{
    [JsonPropertyName("version")] public string Version { get; init; } = "";
    [JsonPropertyName("result")] public string Result { get; init; } = "";
    [JsonPropertyName("error")] public string Error { get; init; } = "";
}

/// <summary>
/// Periodic update check. It stages a verified package, then restarts the service when no query is running; the installer
/// scripts apply the package before the next start and roll back if the new version never becomes healthy.
/// </summary>
public sealed class UpdateService : BackgroundService
{
    private readonly UpdateStager _stager;
    private readonly ICentralApi _api;
    private readonly JobRunner _runner;
    private readonly UpdateGate _gate;
    private readonly WorkerOptions _options;
    private readonly IHostApplicationLifetime _lifetime;
    private readonly ILogger<UpdateService> _logger;

    public UpdateService(UpdateStager stager, ICentralApi api, JobRunner runner, UpdateGate gate, WorkerOptions options,
        IHostApplicationLifetime lifetime, ILogger<UpdateService> logger)
    {
        _stager = stager; _api = api; _runner = runner; _gate = gate; _options = options; _lifetime = lifetime; _logger = logger;
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        if (_options.UpdateCheckMinutes == 0) return;
        try
        {
            await ReportOutcomeAsync(stoppingToken);
            await Task.Delay(TimeSpan.FromSeconds(_options.UpdateStartDelaySeconds + (_options.UpdateStartDelaySeconds >= 30 ? Random.Shared.Next(30) : 0)), stoppingToken);  // let the first heartbeats land
            await ConfirmHealthyAsync(stoppingToken);
            while (!stoppingToken.IsCancellationRequested)
            {
                try
                {
                    if (await _stager.TryStageAsync(stoppingToken)) _gate.RequestRestart();
                }
                catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException && !stoppingToken.IsCancellationRequested)
                {
                    _logger.LogWarning("Update check failed: {Error}", ex.GetType().Name);
                }
                if (_gate.RestartPending)
                {
                    while (_runner.IsBusy && !stoppingToken.IsCancellationRequested) await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken);
                    _logger.LogInformation("Restarting to apply the staged update");
                    Environment.ExitCode = 75;  // non-zero on purpose: Windows recovery and systemd both restart the service
                    _lifetime.StopApplication();
                    return;
                }
                await Task.Delay(_options.UpdateCheckSecondsOverride > 0 ? TimeSpan.FromSeconds(_options.UpdateCheckSecondsOverride) : TimeSpan.FromMinutes(_options.UpdateCheckMinutes), stoppingToken);
            }
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
    }

    private async Task ReportOutcomeAsync(CancellationToken ct)
    {
        var path = Path.Combine(_options.EffectiveUpdateDirectory, "last-applied.json");
        try
        {
            if (!File.Exists(path)) return;
            var outcome = JsonSerializer.Deserialize<ApplyOutcome>(await File.ReadAllTextAsync(path, ct));
            if (outcome is not null && UpdateStager.TryParseVersion(outcome.Version, out _) && outcome.Result is "APPLIED" or "ROLLED_BACK" or "FAILED")
            {
                await _api.ReportUpdateAsync(outcome.Version, outcome.Result, outcome.Error ?? "", ct);
                File.Delete(path);
            }
        }
        catch (Exception ex) when (ex is JsonException or IOException or UnauthorizedAccessException or HttpRequestException or CentralApiException)
        {
            _logger.LogWarning("Could not report the last update outcome: {Error}", ex.GetType().Name);
        }
    }

    /// <summary>The marker the apply script waits for before it stops watching this version (probation).</summary>
    private async Task ConfirmHealthyAsync(CancellationToken ct)
    {
        for (var i = 0; i < 24 && !_gate.Healthy; i++) await Task.Delay(TimeSpan.FromSeconds(5), ct);
        if (!_gate.Healthy) return;
        try
        {
            Directory.CreateDirectory(_options.EffectiveUpdateDirectory);
            await File.WriteAllTextAsync(Path.Combine(_options.EffectiveUpdateDirectory, "confirmed"), WorkerOptions.Version, ct);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _logger.LogWarning("Could not write the health marker: {Error}", ex.GetType().Name);
        }
    }
}
