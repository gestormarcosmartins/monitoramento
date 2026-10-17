using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
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

/// <summary>
/// Mescla o que o formulário gerencia com o agentsettings.json que já existe, em vez de reescrever o arquivo do zero.
/// Preserva chaves desconhecidas (inclusive TenantId e campos futuros), outras fontes de negócio e as fontes de uma
/// configuração desabilitada; troca de nome lógico substitui a fonte antiga em vez de duplicá-la.
/// </summary>
public static class AgentSettingsMerge
{
    private static readonly string[] BusinessKeys = ["BusinessEnabled", "BusinessSources"];

    public static string Merge(string? existingJson, object agentValues, BusinessSource? businessSource, string loadedRef)
    {
        JsonObject root = new();
        if (!string.IsNullOrWhiteSpace(existingJson))
        {
            try { root = JsonNode.Parse(existingJson)?.AsObject() ?? new JsonObject(); }
            catch (JsonException) { root = new JsonObject(); }
        }
        var agent = root["Agent"] as JsonObject ?? new JsonObject();
        var incoming = JsonSerializer.SerializeToNode(agentValues)!.AsObject();
        foreach (var item in incoming.ToList())
        {
            if (BusinessKeys.Contains(item.Key)) continue;
            // o TenantId é preenchido pelo cadastro do agente: um valor vazio do formulário nunca apaga o existente
            if (item.Key == "TenantId" && !string.IsNullOrWhiteSpace(agent["TenantId"]?.ToString())) continue;
            incoming.Remove(item.Key);
            agent[item.Key] = item.Value;
        }
        agent["BusinessEnabled"] = businessSource is not null;
        agent["BusinessSources"] = MergeSources(agent["BusinessSources"] as JsonArray, businessSource, loadedRef);
        root["Agent"] = agent;
        return root.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
    }

    private static JsonArray MergeSources(JsonArray? existing, BusinessSource? current, string loadedRef)
    {
        var result = new JsonArray();
        var placed = false;
        foreach (var node in existing?.OfType<JsonObject>().ToList() ?? new List<JsonObject>())
        {
            var reference = node["Ref"]?.ToString() ?? "";
            if (current is not null && (reference == current.Ref || (loadedRef != "" && reference == loadedRef)))
            {
                if (!placed) { result.Add(JsonSerializer.SerializeToNode(current)); placed = true; }
                continue;  // a fonte antiga (mesmo nome ou nome anterior) é substituída
            }
            result.Add(node.DeepClone());  // outras fontes e a fonte de uma configuração desabilitada ficam como estavam
        }
        if (!placed && current is not null) result.Add(JsonSerializer.SerializeToNode(current));
        return result;
    }
}

/// <summary>Senha da fonte: a digitada, senão a já salva para o nome, senão a do nome anterior (renomear não pode perder a senha).</summary>
public static class BusinessPasswordChoice
{
    public static string Effective(string typed, string savedForRef, string savedForPreviousRef) =>
        !string.IsNullOrEmpty(typed) ? typed : !string.IsNullOrEmpty(savedForRef) ? savedForRef : savedForPreviousRef;
}
