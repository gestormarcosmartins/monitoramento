namespace Nexora.Agent.Benner;

/// <summary>Seam for a future database engine (PostgreSQL, MySQL, ...) to plug in its own
/// equivalent of the 19 Benner health checks without touching SqlServerCollector or anything
/// else in the agent. <see cref="BennerSqlServerDiagnostics"/> is the only implementation today;
/// a future engine would add its own class implementing this interface and be wired up in
/// Program.cs/AgentWorker.cs the same way, keyed off its own "enabled" flag.</summary>
public interface IBennerEngineDiagnostics
{
    Task<BennerSnapshotPayload?> CollectAsync(CancellationToken cancellationToken);
}
