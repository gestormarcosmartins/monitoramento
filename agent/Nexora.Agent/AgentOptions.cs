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
    public string[] EssentialServices { get; set; } = ["MSSQLSERVER", "SQLSERVERAGENT", "NexoraAgent"];
    public bool SqlEnabled { get; set; } = OperatingSystem.IsWindows();
    public string SqlServer { get; set; } = "localhost";
    public string SqlDatabase { get; set; } = "master";
    public bool SqlIntegratedSecurity { get; set; } = false;
    public string SqlUser { get; set; } = "";
    public bool SqlEncrypt { get; set; } = true;
    public bool SqlTrustServerCertificate { get; set; } = true;
    public int SqlCommandTimeoutSeconds { get; set; } = 30;
    public string ChaveouConnectionString { get; set; } = "";
    public string ChaveouTenantPublicId { get; set; } = "";
    public int ChaveouOperationalSeconds { get; set; } = 60;
    public int ChaveouPerformanceSeconds { get; set; } = 21600;
    public int ChaveouCommandTimeoutSeconds { get; set; } = 30;
}
