using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Nexora.Agent;

public sealed class CredentialStore
{
    private readonly string _directory;
    private string FileName => Path.Combine(_directory, "credentials.dat");

    public CredentialStore(Microsoft.Extensions.Options.IOptions<AgentOptions> options) =>
        _directory = AgentPaths.DataDirectory(options.Value);

    public Credentials? Load()
    {
        if (!File.Exists(FileName)) return null;
        var storedBytes = File.ReadAllBytes(FileName);
        var bytes = OperatingSystem.IsWindows()
            ? ProtectedData.Unprotect(storedBytes, null, DataProtectionScope.LocalMachine)
            : storedBytes;
        return JsonSerializer.Deserialize<Credentials>(bytes);
    }

    public void Save(Credentials credentials)
    {
        Directory.CreateDirectory(_directory);
        var bytes = JsonSerializer.SerializeToUtf8Bytes(credentials);
        var storedBytes = OperatingSystem.IsWindows()
            ? ProtectedData.Protect(bytes, null, DataProtectionScope.LocalMachine)
            : bytes;
        File.WriteAllBytes(FileName, storedBytes);
        if (!OperatingSystem.IsWindows())
            File.SetUnixFileMode(FileName, UnixFileMode.UserRead | UnixFileMode.UserWrite);
    }
}
