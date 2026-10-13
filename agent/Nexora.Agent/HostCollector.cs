using System.Diagnostics;
using System.Net.NetworkInformation;
using System.ServiceProcess;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Nexora.Agent;

public readonly record struct MemoryInfo(
    [property: System.Text.Json.Serialization.JsonPropertyName("load_percent")] double LoadPercent,
    [property: System.Text.Json.Serialization.JsonPropertyName("total_bytes")] ulong TotalBytes,
    [property: System.Text.Json.Serialization.JsonPropertyName("available_bytes")] ulong AvailableBytes,
    [property: System.Text.Json.Serialization.JsonPropertyName("used_bytes")] ulong UsedBytes);

public readonly record struct ServiceStatus(
    [property: System.Text.Json.Serialization.JsonPropertyName("name")] string Name,
    [property: System.Text.Json.Serialization.JsonPropertyName("state")] string State,
    [property: System.Text.Json.Serialization.JsonPropertyName("ok")] bool Ok,
    [property: System.Text.Json.Serialization.JsonPropertyName("sub_state")] string? SubState = null,
    [property: System.Text.Json.Serialization.JsonPropertyName("load_state")] string? LoadState = null,
    [property: System.Text.Json.Serialization.JsonPropertyName("error")] string? Error = null);

public sealed class HostCollector
{
    private ulong _idle, _kernel, _user;
    private ulong _linuxIdle, _linuxTotal;
    [StructLayout(LayoutKind.Sequential)] private struct MemoryStatus { public uint Length; public uint Load; public ulong TotalPhysical,AvailablePhysical,TotalPageFile,AvailablePageFile,TotalVirtual,AvailableVirtual,AvailableExtendedVirtual; }
    [DllImport("kernel32.dll")] private static extern bool GlobalMemoryStatusEx(ref MemoryStatus status);
    [DllImport("kernel32.dll")] private static extern bool GetSystemTimes(out long idle,out long kernel,out long user);

    private double? CpuPercent()
    {
        if (OperatingSystem.IsLinux()) return LinuxCpuPercent();
        if(!OperatingSystem.IsWindows()||!GetSystemTimes(out var idleRaw,out var kernelRaw,out var userRaw))return null;
        var idle=unchecked((ulong)idleRaw);var kernel=unchecked((ulong)kernelRaw);var user=unchecked((ulong)userRaw);
        var total=(kernel-_kernel)+(user-_user);var busy=total-(idle-_idle);double? result=_kernel==0||total==0?(double?)null:Math.Round(busy*100d/total,1);_idle=idle;_kernel=kernel;_user=user;return result;
    }

    private double? LinuxCpuPercent()
    {
        try
        {
            var fields = File.ReadLines("/proc/stat").First().Split(' ', StringSplitOptions.RemoveEmptyEntries).Skip(1).Select(ulong.Parse).ToArray();
            if (fields.Length < 4) return null;
            var idle = fields[3] + (fields.Length > 4 ? fields[4] : 0);
            var total = fields.Aggregate(0UL, (sum, value) => sum + value);
            var deltaTotal = total - _linuxTotal;
            var result = _linuxTotal == 0 || deltaTotal == 0 ? (double?)null : Math.Round((deltaTotal - (idle - _linuxIdle)) * 100d / deltaTotal, 1);
            _linuxIdle = idle;
            _linuxTotal = total;
            return result;
        }
        catch { return null; }
    }

    private MemoryInfo? Memory()
    {
        if (OperatingSystem.IsLinux()) return LinuxMemory();
        if(!OperatingSystem.IsWindows())return null;var status=new MemoryStatus{Length=(uint)Marshal.SizeOf<MemoryStatus>()};if(!GlobalMemoryStatusEx(ref status))return null;
        return new MemoryInfo(status.Load, status.TotalPhysical, status.AvailablePhysical, status.TotalPhysical-status.AvailablePhysical);
    }

    private static MemoryInfo? LinuxMemory()
    {
        try
        {
            var values = File.ReadLines("/proc/meminfo")
                .Select(line => line.Split(':', 2))
                .Where(parts => parts.Length == 2)
                .ToDictionary(parts => parts[0], parts => ulong.Parse(parts[1].Trim().Split(' ')[0]) * 1024UL);
            var total = values["MemTotal"];
            var available = values.GetValueOrDefault("MemAvailable", values.GetValueOrDefault("MemFree"));
            var used = total - available;
            return new MemoryInfo(Math.Round(used * 100d / Math.Max(1UL,total),1), total, available, used);
        }
        catch { return null; }
    }

    public Dictionary<string, object?> Metrics()
    {
        var process = Process.GetCurrentProcess();
        return new Dictionary<string, object?>
        {
            ["machine"] = Environment.MachineName,
            ["os"] = Environment.OSVersion.VersionString,
            ["uptime_seconds"] = Environment.TickCount64 / 1000,
            ["processors"] = Environment.ProcessorCount,
            ["cpu_percent"] = CpuPercent(),
            ["memory"] = Memory(),
            ["agent_working_set_mb"] = Math.Round(process.WorkingSet64 / 1024d / 1024d, 1),
            ["drives"] = DriveInfo.GetDrives().Where(d => d.IsReady).Select(d => new {
                name=d.Name, format=d.DriveFormat, total_bytes=d.TotalSize, free_bytes=d.AvailableFreeSpace,
                free_percent=Math.Round(d.AvailableFreeSpace * 100d / Math.Max(1,d.TotalSize),1)
            }).ToArray(),
            ["networks"] = NetworkInterface.GetAllNetworkInterfaces().Where(n => n.OperationalStatus==OperationalStatus.Up)
                .Select(n => { var s=n.GetIPv4Statistics(); return new { name=n.Name,type=n.NetworkInterfaceType.ToString(),speed=n.Speed,received_bytes=s.BytesReceived,sent_bytes=s.BytesSent,receive_errors=s.IncomingPacketsWithErrors,send_errors=s.OutgoingPacketsWithErrors }; }).ToArray()
        };
    }

    public ServiceStatus[] Services(IEnumerable<string> names)
    {
        return names.Where(n => !string.IsNullOrWhiteSpace(n)).Distinct(StringComparer.OrdinalIgnoreCase)
        .Select(name => {
            try
            {
                if (OperatingSystem.IsWindows())
                {
                    using var service = new ServiceController(name.Trim());
                    return new ServiceStatus(name.Trim(), service.Status.ToString().ToLowerInvariant(), service.Status==ServiceControllerStatus.Running);
                }
                if (OperatingSystem.IsLinux()) return LinuxService(name.Trim());
                return new ServiceStatus(name.Trim(), "unsupported", false);
            }
            catch (Exception error) { return new ServiceStatus(name.Trim(), "unknown", false, Error: error.Message); }
        }).ToArray();
    }

    /// <summary>Compares host metrics/services against the agent's configured push monitors (codes
    /// host-cpu, host-memory, host-disk, host-services) so they show up as monitor cards, the same
    /// way SqlServerCollector.OperationalAsync does for SQL checks.</summary>
    public SnapshotEnvelope? Operational(AgentConfiguration config, IEnumerable<string> essentialServiceNames, string agentName)
    {
        // GET /agent/config returns every 'push' monitor for the whole tenant, not just
        // this agent's — with several Windows agents in the same tenant, a bare code like
        // "host-cpu" collides across machines (ToDictionary used to throw ArgumentException
        // on the duplicate key). Monitor codes are provisioned as "<code>:<agent name>" to
        // stay unique per machine; last-write-wins on a collision is fine here, it would
        // only happen for two agents sharing the exact same name.
        var monitors = config.Monitors.Select(m=>new {m,code=Code(m.Configuration)}).Where(x=>x.code is not null)
            .GroupBy(x=>x.code!, StringComparer.OrdinalIgnoreCase).ToDictionary(g=>g.Key,g=>g.Last().m,StringComparer.OrdinalIgnoreCase);
        if (monitors.Count == 0) return null;
        var now = DateTimeOffset.UtcNow;
        var results = new List<AgentResult>();
        void Add(string code, bool ok, string message) { if (monitors.TryGetValue($"{code}:{agentName}", out var monitor)) results.Add(new(monitor.Id, ok, ok ? "up" : "down", message, now)); }
        // Each check runs independently: on a machine where, say, DriveInfo.GetDrives()
        // throws or hangs-then-throws (observed on a Hyper-V host), one failure must not
        // silently drop the other three results along with it (Add() for the earlier ones
        // had already been skipped since the exception unwinds before they run).
        void AddSafe(string code, Func<(bool ok,string message)?> evaluate) {
            try { var result=evaluate(); if (result is { } r) Add(code,r.ok,r.message); }
            catch (Exception error) { if (monitors.TryGetValue($"{code}:{agentName}", out var monitor)) results.Add(new(monitor.Id,false,"unknown",error.Message,now)); }
        }

        AddSafe("host-cpu", () => { var cpu=CpuPercent(); return cpu is null ? null : (cpu<90, $"Uso de CPU: {cpu}%"); });

        AddSafe("host-memory", () => { var memory=Memory(); return memory is null ? null : (memory.Value.LoadPercent<90, $"Uso de memória: {memory.Value.LoadPercent}%"); });

        AddSafe("host-disk", () => {
            var drives = DriveInfo.GetDrives().Where(d => d.IsReady).ToArray();
            if (drives.Length == 0) return null;
            var low = drives.Where(d => d.AvailableFreeSpace * 100d / Math.Max(1, d.TotalSize) < 10).Select(d => d.Name).ToArray();
            return (low.Length == 0, low.Length == 0 ? "Espaço em disco normal em todas as unidades" : $"Pouco espaço livre: {string.Join(", ", low)}");
        });

        AddSafe("host-services", () => {
            var serviceNames = essentialServiceNames.Where(n => !string.IsNullOrWhiteSpace(n)).ToArray();
            if (serviceNames.Length == 0) return null;
            var statuses = Services(serviceNames);
            var down = statuses.Where(s => !s.Ok).Select(s => s.Name).ToArray();
            return (down.Length == 0, down.Length == 0 ? "Todos os serviços essenciais estão ativos" : $"Serviço(s) parado(s): {string.Join(", ", down)}");
        });

        return results.Count == 0 ? null : new($"host-health-{now:yyyyMMddHHmmss}-{Guid.NewGuid():N}", now, results, new(){["source"]="windows-agent-direct",["profile"]="host_health"});
    }

    private static string? Code(JsonElement e) => e.ValueKind==JsonValueKind.Object&&(e.TryGetProperty("code",out var c)||e.TryGetProperty("check_id",out c))?c.ToString():null;

    private static ServiceStatus LinuxService(string name)
    {
        if (!Regex.IsMatch(name, "^[A-Za-z0-9_.@-]+$"))
            return new ServiceStatus(name, "invalid", false, Error: "Nome de unidade systemd inválido.");
        var unit = name.EndsWith(".service", StringComparison.OrdinalIgnoreCase) ? name : $"{name}.service";
        using var process = new Process { StartInfo = new ProcessStartInfo("systemctl") { RedirectStandardOutput=true, RedirectStandardError=true, UseShellExecute=false, CreateNoWindow=true } };
        process.StartInfo.ArgumentList.Add("show");
        process.StartInfo.ArgumentList.Add(unit);
        process.StartInfo.ArgumentList.Add("--property=LoadState,ActiveState,SubState");
        process.StartInfo.ArgumentList.Add("--value");
        process.Start();
        var output = process.StandardOutput.ReadToEnd();
        var error = process.StandardError.ReadToEnd().Trim();
        if (!process.WaitForExit(5000)) { process.Kill(true); return new ServiceStatus(unit, "timeout", false); }
        var states = output.Split(['\r','\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var load = states.ElementAtOrDefault(0) ?? "unknown";
        var active = states.ElementAtOrDefault(1) ?? "unknown";
        var sub = states.ElementAtOrDefault(2) ?? "unknown";
        return new ServiceStatus(unit, active, process.ExitCode==0 && load=="loaded" && active=="active", sub, load, string.IsNullOrWhiteSpace(error)?null:error);
    }
}
