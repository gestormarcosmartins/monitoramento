using System.Text.RegularExpressions;

namespace Nexora.BusinessWorker;

/// <summary>
/// Resolves a data source reference to its connection string ON THE CUSTOMER SIDE. The central only knows the
/// reference (for example "erp_principal"); user and password never travel in a job and are never logged.
/// </summary>
public sealed partial class SecretResolver
{
    private readonly WorkerOptions _options;
    private readonly Func<string, string?> _environment;

    public SecretResolver(WorkerOptions options, Func<string, string?>? environment = null)
    {
        _options = options;
        _environment = environment ?? Environment.GetEnvironmentVariable;
    }

    [GeneratedRegex("^[a-z][a-z0-9_]{1,63}$")]
    private static partial Regex RefPattern();

    public string? ConnectionString(string databaseRef)
    {
        if (!RefPattern().IsMatch(databaseRef)) return null;  // also blocks path traversal in the file lookup below
        var fromEnvironment = _environment($"NEXORA_BW_DB_{databaseRef.ToUpperInvariant()}");
        if (!string.IsNullOrWhiteSpace(fromEnvironment)) return fromEnvironment.Trim();
        if (string.IsNullOrWhiteSpace(_options.SecretsDirectory)) return null;
        var path = Path.Combine(_options.SecretsDirectory, databaseRef + ".conn");
        return File.Exists(path) ? File.ReadAllText(path).Trim() : null;
    }

    public string? WorkerToken()
    {
        var fromEnvironment = _environment("NEXORA_BW_TOKEN");
        if (!string.IsNullOrWhiteSpace(fromEnvironment)) return fromEnvironment.Trim();
        return !string.IsNullOrWhiteSpace(_options.TokenFile) && File.Exists(_options.TokenFile)
            ? File.ReadAllText(_options.TokenFile).Trim() : null;
    }
}
