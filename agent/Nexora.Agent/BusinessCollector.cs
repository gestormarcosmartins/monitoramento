using System.Text.Json;

namespace Nexora.Agent;

/// <summary>Executes approved connector manifests; persists every sample before sending.</summary>
public sealed class BusinessCollector
{
    private readonly BusinessReader _reader;
    private readonly BusinessQueue _queue;
    private readonly CentralClient _cloud;
    private readonly ILogger<BusinessCollector> _logger;
    private readonly Dictionary<string,DateTimeOffset> _nextRuns=new();

    public BusinessCollector(BusinessReader reader,BusinessQueue queue,CentralClient cloud,ILogger<BusinessCollector> logger)
    {_reader=reader;_queue=queue;_cloud=cloud;_logger=logger;}

    public async Task CollectAsync(AgentConfiguration config,CancellationToken token)
    {
        if(config.Configuration.ValueKind!=JsonValueKind.Object ||
           !config.Configuration.TryGetProperty("business_collectors",out var jobs) ||
           jobs.ValueKind!=JsonValueKind.Array) return;
        foreach(var job in jobs.EnumerateArray())
        {
            string? indicator=null;
            try
            {
                indicator=job.GetProperty("indicator_id").GetString();
                if(!Guid.TryParse(indicator,out _))throw new InvalidDataException("Indicator id invalid");
                var key=job.TryGetProperty("collector_id",out var id)?id.GetString()??indicator!:indicator!;
                var period=Math.Clamp(job.GetProperty("poll_seconds").GetInt32(),60,86400);
                var now=DateTimeOffset.UtcNow;
                if(_nextRuns.TryGetValue(key,out var next) && now<next)continue;
                // Failed collections retry at the next run; repeated timer errors never overflow data.
                _nextRuns[key]=now.AddSeconds(period);
                var value=await _reader.ReadAsync(job,token);
                var eventId=$"bm-{key}-{now.ToUnixTimeMilliseconds()}-{Guid.NewGuid():N}";
                var collectorId=job.TryGetProperty("collector_id",out var collectorField)?collectorField.GetString():null;
                int? revision=job.TryGetProperty("revision",out var revisionField)?revisionField.GetInt32():null;
                await _queue.StoreAsync(new BusinessSample(indicator!,eventId,value,now,collectorId,revision),token);
            }
            catch(Exception ex) when(!token.IsCancellationRequested)
            {
                _logger.LogWarning("Business collection failed: {Indicator} {ErrorCode}",
                    indicator??"unassigned",ex.GetType().Name);
            }
        }
    }

    public async Task FlushAsync(CancellationToken token)
    {
        foreach(var sample in await _queue.PendingAsync(100,token))
        {
            try
            {
                await _cloud.SendBusinessAsync(sample,token);
                await _queue.CompleteAsync(sample.EventId,token);
            }
            catch(HttpRequestException ex) when(!token.IsCancellationRequested && IsPermanentRejection(ex.StatusCode))
            {
                // 4xx that cannot succeed on retry (e.g. 403/404/409/422): quarantine and keep flowing.
                await _queue.QuarantineAsync(sample.EventId,$"Http{(int)ex.StatusCode!}",token);
                _logger.LogError("Business event quarantined: Http{Status} Indicator={Indicator}",
                    (int)ex.StatusCode!,sample.IndicatorId);
            }
            catch(Exception ex) when(!token.IsCancellationRequested)
            {
                _logger.LogWarning("Business event backlog retained: {ErrorCode} Depth={Depth}",
                    ex.GetType().Name,_queue.Depth());
                break; // Not drop or spin on non-acknowledged event.
            }
        }
    }
    // 401/408/429 and 5xx are transient or credential issues: keep the event and retry later.
    private static bool IsPermanentRejection(System.Net.HttpStatusCode? status) =>
        status is System.Net.HttpStatusCode.BadRequest or System.Net.HttpStatusCode.Forbidden
            or System.Net.HttpStatusCode.NotFound or System.Net.HttpStatusCode.Conflict
            or System.Net.HttpStatusCode.Gone or System.Net.HttpStatusCode.UnprocessableEntity;

    public int BacklogDepth()=>_queue.Depth();
}
