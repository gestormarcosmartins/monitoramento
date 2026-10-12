using System.Diagnostics;
using System.Net.NetworkInformation;
using System.ServiceProcess;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

namespace Nexora.Agent;

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

    private object? Memory()
    {
        if (OperatingSystem.IsLinux()) return LinuxMemory();
        if(!OperatingSystem.IsWindows())return null;var status=new MemoryStatus{Length=(uint)Marshal.SizeOf<MemoryStatus>()};if(!GlobalMemoryStatusEx(ref status))return null;
        return new { load_percent=status.Load,total_bytes=status.TotalPhysical,available_bytes=status.AvailablePhysical,used_bytes=status.TotalPhysical-status.AvailablePhysical };
    }

    private static object? LinuxMemory()
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
            return new { load_percent=Math.Round(used * 100d / Math.Max(1UL,total),1),total_bytes=total,available_bytes=available,used_bytes=used };
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

    public object[] Services(IEnumerable<string> names)
    {
        return names.Where(n => !string.IsNullOrWhiteSpace(n)).Distinct(StringComparer.OrdinalIgnoreCase)
        .Select(name => {
            try
            {
                if (OperatingSystem.IsWindows())
                {
                    using var service = new ServiceController(name.Trim());
                    return (object)new { name=name.Trim(), state=service.Status.ToString().ToLowerInvariant(), ok=service.Status==ServiceControllerStatus.Running };
                }
                if (OperatingSystem.IsLinux()) return LinuxService(name.Trim());
                return (object)new { name=name.Trim(), state="unsupported", ok=false };
            }
            catch (Exception error) { return (object)new { name=name.Trim(), state="unknown", ok=false, error=error.Message }; }
        }).ToArray();
    }

    private static object LinuxService(string name)
    {
        if (!Regex.IsMatch(name, "^[A-Za-z0-9_.@-]+$"))
            return new { name, state="invalid", ok=false, error="Nome de unidade systemd inválido." };
        var unit = name.EndsWith(".service", StringComparison.OrdinalIgnoreCase) ? name : $"{name}.service";
        using var process = new Process { StartInfo = new ProcessStartInfo("systemctl") { RedirectStandardOutput=true, RedirectStandardError=true, UseShellExecute=false, CreateNoWindow=true } };
        process.StartInfo.ArgumentList.Add("show");
        process.StartInfo.ArgumentList.Add(unit);
        process.StartInfo.ArgumentList.Add("--property=LoadState,ActiveState,SubState");
        process.StartInfo.ArgumentList.Add("--value");
        process.Start();
        var output = process.StandardOutput.ReadToEnd();
        var error = process.StandardError.ReadToEnd().Trim();
        if (!process.WaitForExit(5000)) { process.Kill(true); return new { name=unit, state="timeout", ok=false }; }
        var states = output.Split(['\r','\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        var load = states.ElementAtOrDefault(0) ?? "unknown";
        var active = states.ElementAtOrDefault(1) ?? "unknown";
        var sub = states.ElementAtOrDefault(2) ?? "unknown";
        return new { name=unit, state=active, sub_state=sub, load_state=load, ok=process.ExitCode==0 && load=="loaded" && active=="active", error=string.IsNullOrWhiteSpace(error)?null:error };
    }
}
