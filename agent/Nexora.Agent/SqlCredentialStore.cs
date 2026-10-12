using System.Security.Cryptography;
using System.Text;

namespace Nexora.Agent;

public sealed class SqlCredentialStore
{
    public static string FileName => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "Nexora", "Agent", "sql-password.dat");

    public string Load()
    {
        if (!File.Exists(FileName)) return "";
        var encrypted = File.ReadAllBytes(FileName);
        var clear = OperatingSystem.IsWindows()
            ? ProtectedData.Unprotect(encrypted, null, DataProtectionScope.LocalMachine)
            : encrypted;
        return Encoding.UTF8.GetString(clear);
    }
}
