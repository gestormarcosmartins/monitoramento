using System.Data.Common;
using System.Diagnostics;
using Microsoft.Extensions.Logging;

namespace Nexora.BusinessWorker;

/// <summary>
/// One job from verification to report. Logs carry identifiers, codes and durations only: never the SQL text,
/// the parameters, the returned values, a connection string or a token.
/// </summary>
public sealed class JobRunner
{
    private static readonly TimeSpan RenewEvery = TimeSpan.FromSeconds(45);
    private readonly ICentralApi _api;
    private readonly IQueryExecutor _executor;
    private readonly SecretResolver _secrets;
    private readonly WorkerOptions _options;
    private readonly ILogger<JobRunner> _logger;
    private readonly TimeProvider _time;
    private readonly TimeSpan[] _resultRetryDelays;
    private int _running;
    /// <summary>True while a job is between verification and its final report (a restart must wait for it).</summary>
    public bool IsBusy => Volatile.Read(ref _running) > 0;

    public JobRunner(ICentralApi api, IQueryExecutor executor, SecretResolver secrets, WorkerOptions options,
        ILogger<JobRunner> logger, TimeProvider? time = null, TimeSpan[]? resultRetryDelays = null)
    {
        _api = api; _executor = executor; _secrets = secrets; _options = options; _logger = logger;
        _time = time ?? TimeProvider.System;
        _resultRetryDelays = resultRetryDelays ?? new[] { TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(10) };
    }

    public async Task RunAsync(JobEnvelope envelope, CancellationToken stop)
    {
        Interlocked.Increment(ref _running);
        try { await RunCoreAsync(envelope, stop); }
        finally { Interlocked.Decrement(ref _running); }
    }

    private async Task RunCoreAsync(JobEnvelope envelope, CancellationToken stop)
    {
        Job job;
        try
        {
            job = JobVerifier.Verify(envelope, _options, _time.GetUtcNow());
        }
        catch (JobRejectedException rejected)
        {
            _logger.LogWarning("Job rejected before execution: {Code}", rejected.Code);
            // Only a job addressed to this worker can be closed; anything else is dropped (the lease then lapses).
            if (JobVerifier.TryReadExecutionId(envelope.Payload, _options) is { } id)
                await TryReportAsync(id, new FailureBody { Status = "REJECTED", ErrorCode = rejected.Code, Retryable = false }, stop);
            return;
        }

        if (!await _api.StartedAsync(job.ExecutionId, stop))
        {
            _logger.LogWarning("Lease no longer valid for execution {ExecutionId}; skipping", job.ExecutionId);
            return;
        }

        using var renewal = CancellationTokenSource.CreateLinkedTokenSource(stop);
        var renewer = RenewLoopAsync(job, renewal.Token);
        var clock = Stopwatch.StartNew();
        try
        {
            var connection = _secrets.ConnectionString(job.DatabaseRef) ?? throw new JobRejectedException("database_ref_not_configured");
            var result = await _executor.ExecuteAsync(job, connection, stop);
            renewal.Cancel();
            await ReportResultAsync(job, result, stop);
            _logger.LogInformation("Execution {ExecutionId} succeeded in {Ms} ms, {Rows} rows, truncated={Truncated}",
                job.ExecutionId, result.DurationMs, result.RowCount, result.Truncated);
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested)
        {
            // Shutting down: the lease lapses and the central requeues the job.
            _logger.LogWarning("Execution {ExecutionId} interrupted by shutdown", job.ExecutionId);
        }
        catch (Exception exception)
        {
            renewal.Cancel();
            var failure = Classify(exception, (int)clock.ElapsedMilliseconds);
            _logger.LogWarning("Execution {ExecutionId} failed: {Code} (retryable={Retryable})", job.ExecutionId, failure.ErrorCode, failure.Retryable);
            await TryReportAsync(job.ExecutionId, failure, stop);
        }
        finally
        {
            renewal.Cancel();
            try { await renewer; } catch (OperationCanceledException) { }
        }
    }

    /// <summary>Maps an exception to a sanitized failure: type and provider code only, never driver messages (they can echo data).</summary>
    internal static FailureBody Classify(Exception exception, int durationMs) => exception switch
    {
        JobRejectedException r => new FailureBody { Status = "REJECTED", ErrorCode = r.Code, Retryable = false, DurationMs = durationMs },
        ResultContractException c => new FailureBody { Status = "FAILED", ErrorCode = c.Code, Retryable = false, DurationMs = durationMs },
        TimeoutException => new FailureBody { Status = "TIMED_OUT", ErrorCode = "query_timeout", Retryable = true, DurationMs = durationMs },
        DbException d => new FailureBody
        {
            Status = "FAILED", ErrorCode = DbCode(d),
            ErrorMessage = d.GetType().Name, Retryable = d.IsTransient, DurationMs = durationMs,
        },
        _ => new FailureBody { Status = "FAILED", ErrorCode = "unexpected_error", ErrorMessage = exception.GetType().Name, Retryable = false, DurationMs = durationMs },
    };

    private static string DbCode(DbException d) => d switch
    {
        Npgsql.PostgresException p => $"pg_{p.SqlState}",
        Microsoft.Data.SqlClient.SqlException s => $"mssql_{s.Number}",
        _ => "db_error",
    };

    private async Task RenewLoopAsync(Job job, CancellationToken token)
    {
        var renewals = 0;
        try
        {
            while (renewals < 2)
            {
                await Task.Delay(RenewEvery, _time, token);
                if (!await _api.RenewAsync(job.ExecutionId, token)) return;
                renewals++;
            }
        }
        catch (OperationCanceledException) { }
        catch (CentralApiException) { }  // renewal is best effort; the lease may simply lapse
        catch (HttpRequestException) { }
    }

    private async Task ReportResultAsync(Job job, ResultBody result, CancellationToken stop)
    {
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                var verdict = await _api.ResultAsync(job.ExecutionId, result, stop);
                if (verdict is "rejected" or "conflict")
                    _logger.LogWarning("Central did not accept execution {ExecutionId}: {Verdict}", job.ExecutionId, verdict);
                return;
            }
            catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException && !stop.IsCancellationRequested)
            {
                if (attempt >= _resultRetryDelays.Length)
                {
                    // Not lost: the lease expires and the central requeues the (read-only) job.
                    _logger.LogError("Could not deliver result for {ExecutionId}; the central will requeue it", job.ExecutionId);
                    return;
                }
                await Task.Delay(_resultRetryDelays[attempt], _time, stop);
            }
        }
    }

    private async Task TryReportAsync(Guid executionId, FailureBody failure, CancellationToken stop)
    {
        try { await _api.FailedAsync(executionId, failure, stop); }
        catch (Exception ex) when (ex is HttpRequestException or CentralApiException or TaskCanceledException)
        {
            _logger.LogError("Could not report failure for {ExecutionId}", executionId);
        }
    }
}
