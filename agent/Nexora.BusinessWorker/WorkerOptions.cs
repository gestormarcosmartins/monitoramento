namespace Nexora.BusinessWorker;

/// <summary>Installation identity and limits. Secrets (token, database credentials) are NOT stored here.</summary>
public sealed class WorkerOptions
{
    public string ServerUrl { get; set; } = "";
    public string TenantId { get; set; } = "";
    public string WorkerId { get; set; } = "";
    /// <summary>Ed25519 public key (base64) pinned at install time; jobs not signed by it are rejected.</summary>
    public string SigningPublicKey { get; set; } = "";
    /// <summary>File with the worker token (alternative to the NEXORA_BW_TOKEN environment variable).</summary>
    public string TokenFile { get; set; } = "";
    /// <summary>Directory with one "ref.conn" file per data source (alternative to the NEXORA_BW_DB_REF variable).</summary>
    public string SecretsDirectory { get; set; } = "";
    public int PollSeconds { get; set; } = 30;
    public int HeartbeatSeconds { get; set; } = 30;
    public bool AllowUntrustedServerCertificate { get; set; }
    /// <summary>Development/homologation only: lets a database on THIS machine (loopback) be reached without TLS.</summary>
    public bool AllowPlaintextLoopbackDatabase { get; set; }
    /// <summary>SQL types without a zone (datetime) are read as UTC when true, otherwise as server-local time.</summary>
    public bool AssumeUtcForUnspecified { get; set; } = true;

    public static string Version => typeof(WorkerOptions).Assembly.GetName().Version?.ToString(3) ?? "0.0.0";

    public IReadOnlyList<string> Validate()
    {
        var problems = new List<string>();
        var validUrl = Uri.TryCreate(ServerUrl, UriKind.Absolute, out var uri)
            && (uri.Scheme == Uri.UriSchemeHttps || (uri.IsLoopback && uri.Scheme == Uri.UriSchemeHttp));
        if (!validUrl) problems.Add("Nexora:ServerUrl must be an https URL (http is accepted only for loopback development).");
        if (!Guid.TryParse(TenantId, out _)) problems.Add("Nexora:TenantId must be a GUID.");
        if (!Guid.TryParse(WorkerId, out _)) problems.Add("Nexora:WorkerId must be a GUID.");
        if (string.IsNullOrWhiteSpace(SigningPublicKey)) problems.Add("Nexora:SigningPublicKey is required (pin the key at install time).");
        if (PollSeconds is < 5 or > 60) problems.Add("Nexora:PollSeconds must be between 5 and 60.");
        if (HeartbeatSeconds is < 10 or > 120) problems.Add("Nexora:HeartbeatSeconds must be between 10 and 120.");
        return problems;
    }
}
