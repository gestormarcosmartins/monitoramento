namespace Nexora.Agent;

public static class AgentPaths
{
    public static string DataDirectory(AgentOptions options)
    {
        if (!string.IsNullOrWhiteSpace(options.DataDirectory))
            return options.DataDirectory;
        return OperatingSystem.IsWindows()
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Nexora", "Agent")
            : "/var/lib/nexora-agent";
    }
}
