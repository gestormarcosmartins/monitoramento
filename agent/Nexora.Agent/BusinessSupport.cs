using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Nexora.Agent;

/// <summary>Senha de cada fonte de negócio, protegida com DPAPI (máquina), no mesmo estilo do SqlCredentialStore.</summary>
public sealed class BusinessSecretStore
{
    private static readonly Regex RefPattern = new("^[a-z][a-z0-9_]{1,63}$");
    public static string Directory => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Nexora", "Agent", "business");

    public static string FileFor(string sourceRef)
    {
        if (!RefPattern.IsMatch(sourceRef)) throw new ArgumentException("Nome lógico inválido.");  // também impede path traversal
        return Path.Combine(Directory, sourceRef + ".dat");
    }

    public string Load(string sourceRef)
    {
        var file = FileFor(sourceRef);
        if (!File.Exists(file)) return "";
        var stored = File.ReadAllBytes(file);
        var clear = OperatingSystem.IsWindows() ? ProtectedData.Unprotect(stored, null, DataProtectionScope.LocalMachine) : stored;
        return Encoding.UTF8.GetString(clear);
    }

    public void Save(string sourceRef, string password)
    {
        System.IO.Directory.CreateDirectory(Directory);
        var clear = Encoding.UTF8.GetBytes(password);
        var stored = OperatingSystem.IsWindows() ? ProtectedData.Protect(clear, null, DataProtectionScope.LocalMachine) : clear;
        File.WriteAllBytes(FileFor(sourceRef), stored);
    }
}

/// <summary>Log em arquivo do Business Worker embutido: so estados e codigos de erro, nunca SQL, valores, senha ou token.</summary>
public static class BusinessLog
{
    private static readonly object Gate = new();
    public static string FilePath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Nexora", "Agent", "logs", "business.log");

    public static void Write(string message)
    {
        try
        {
            lock (Gate)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
                var info = new FileInfo(FilePath);
                if (info.Exists && info.Length > 1_000_000) File.Move(FilePath, FilePath + ".1", true);  // mantem no maximo 2 arquivos
                File.AppendAllText(FilePath, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
            }
        }
        catch (Exception) { /* log nunca pode derrubar o agente */ }
    }
}

public static class BusinessConnection
{
    /// <summary>Monta a string de conexão. Aspas e ponto e vírgula nos campos são recusados para não injetar parâmetros.</summary>
    public static string Build(BusinessSource source, string password)
    {
        foreach (var value in new[] { source.Server, source.Database, source.User, password })
            if (value.Contains(';') || value.Contains('\n') || value.Contains('\r')) throw new ArgumentException("Valor de conexão inválido.");
        if (source.Dialect != "sqlserver") throw new ArgumentException("Somente SQL Server é suportado no agente.");
        var trust = source.TrustServerCertificate ? "TrustServerCertificate=True;" : "";  // o Worker só aceita certificado autoassinado se a conexão pedir
        return $"Server={source.Server};Database={source.Database};User Id={source.User};Password={password};{trust}";
    }
}
