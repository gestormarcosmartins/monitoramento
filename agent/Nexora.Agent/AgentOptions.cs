namespace Nexora.Agent;

public sealed class AgentOptions
{
    public string ServerUrl { get; set; } = "http://localhost:8090";
    public string TenantId { get; set; } = "";
    public string EnrollmentToken { get; set; } = "";
    public string AgentName { get; set; } = Environment.MachineName;
    public string SnapshotDirectory { get; set; } = @"C:\ProgramData\Nexora\Monitor\archive";
    public string DataDirectory { get; set; } = "";
    public int PollSeconds { get; set; } = 30;
    public int HeartbeatSeconds { get; set; } = 60;
    public int ServicesSeconds { get; set; } = 60;
    public int HostMetricsSeconds { get; set; } = 300;
    public int SqlHealthSeconds { get; set; } = 600;
    public int SqlPerformanceSeconds { get; set; } = 21600;
    // "NexoraAgent" was removed from this default: it named the Windows Service this
    // agent used to install as, before switching to a Scheduled Task (SCM broke
    // Microsoft.Data.SqlClient's native SNI library). That service no longer exists
    // by design, so checking for it here always reports a false "down".
    public string[] EssentialServices { get; set; } = ["MSSQLSERVER", "SQLSERVERAGENT"];
    public bool SqlEnabled { get; set; } = OperatingSystem.IsWindows();
    public string SqlServer { get; set; } = "localhost";
    public string SqlDatabase { get; set; } = "master";
    public bool SqlIntegratedSecurity { get; set; } = false;
    public string SqlUser { get; set; } = "";
    public bool SqlEncrypt { get; set; } = true;
    public bool SqlTrustServerCertificate { get; set; } = true;
    public int SqlCommandTimeoutSeconds { get; set; } = 30;
    // Consultas de negócio (Business Worker embutido). Desligado por padrão: um agente que não usa isso não muda em nada.
    public bool BusinessEnabled { get; set; } = false;
    public BusinessSource[] BusinessSources { get; set; } = [];

    public string ChaveouConnectionString { get; set; } = "";
    public string ChaveouTenantPublicId { get; set; } = "";
    public int ChaveouOperationalSeconds { get; set; } = 60;
    public int ChaveouPerformanceSeconds { get; set; } = 21600;
    public int ChaveouCommandTimeoutSeconds { get; set; } = 30;

    // Benner health-monitor port (see Benner/BennerSqlServerDiagnostics.cs): 19 detailed SQL
    // Server checks pushed straight to POST {ServerUrl}/api/v1/agents/benner/snapshots, bypassing
    // the normal LocalQueue/SnapshotEnvelope/monitor-matching pipeline entirely, since that
    // endpoint uses its own auth scheme (a single shared ingest token + tenant id, not this
    // agent's per-install enrollment token/id) and its own payload shape. Opt-in and disabled
    // by default because most agent installs don't run the Benner ERP's SQL Server.
    public bool BennerEnabled { get; set; } = false;
    // Matches the Python collector's own 30-minute cadence (and the fixed interval_seconds=1800
    // Nexora's benner.py bakes into the cm_monitors rows it creates for each check).
    public int BennerIntervalSeconds { get; set; } = 1800;
    // Shared secret validated server-side against settings().benner_ingest_token - NOT the
    // per-agent EnrollmentToken/Credentials.AgentToken used by CentralClient's other calls.
    public string BennerIngestToken { get; set; } = "";
    // Tenant GUID sent as X-Tenant-ID for this push; conceptually the same tenant the agent
    // already enrolled under, but kept as its own setting since this endpoint authenticates
    // independently of CentralClient.Credentials (which may not even be populated yet when
    // this fires, and uses a different header set regardless).
    public string BennerTenantId { get; set; } = "";

    // Thresholds below mirror benner_health_monitor/app.py's env-var defaults verbatim
    // (query_timeout_seconds, alert_thresholds(), backup_thresholds(), io_latency_sql()).
    public int BennerQueryTimeoutSeconds { get; set; } = 60;
    public int BennerLatencySampleSeconds { get; set; } = 5;
    public int BennerIoLatencyMinOps { get; set; } = 20;
    public int BennerBackupFullMaxAgeHours { get; set; } = 30;
    public int BennerBackupLogMaxGapMinutes { get; set; } = 45;
    public int BennerBackupLogLookbackHours { get; set; } = 24;
    public int BennerDiskFreeWarnPct { get; set; } = 15;
    public int BennerDiskFreeCritPct { get; set; } = 10;
    public int BennerTempdbUsedWarnPct { get; set; } = 90;
    public int BennerTempdbVersionWarnGb { get; set; } = 5;
    public int BennerTempdbVersionCritGb { get; set; } = 20;
    public int BennerLongTranWarnMinutes { get; set; } = 15;
    public int BennerLongTranCritMinutes { get; set; } = 60;
    public int BennerMemAvailableWarnMb { get; set; } = 4096;
    public int BennerMemAvailableCritMb { get; set; } = 2048;
    public int BennerBlockWarnSeconds { get; set; } = 5;
    public int BennerBlockCritSeconds { get; set; } = 30;
}

/// <summary>Uma fonte de dados de negócio. A senha NÃO fica aqui: vai para o armazenamento protegido (DPAPI).</summary>
public sealed class BusinessSource
{
    /// <summary>Nome lógico usado pela Central (minúsculas, números e _), por exemplo jri_hub.</summary>
    public string Ref { get; set; } = "";
    public string Dialect { get; set; } = "sqlserver";
    public string Server { get; set; } = "";
    public string Database { get; set; } = "";
    public string User { get; set; } = "";
    public bool TrustServerCertificate { get; set; } = false;
}
