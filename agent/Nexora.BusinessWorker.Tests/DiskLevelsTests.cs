using Nexora.Agent;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Disco em dois níveis: atenção entre 10% e 20% livre, crítico abaixo de 10%, um por vez.</summary>
public class DiskLevelsTests
{
    private static DiskLevels.Drive Drive(string name, double freePercent) => new(name, 1000, (long)(freePercent * 10));

    [Fact]
    public void Plenty_of_space_is_ok_on_both_levels()
    {
        var drives = new[] { Drive("C:\\", 55), Drive("D:\\", 90) };
        Assert.True(DiskLevels.Critical(drives).ok);
        Assert.True(DiskLevels.Warning(drives).ok);
    }

    [Fact]
    public void Between_ten_and_twenty_percent_only_the_attention_level_fires()
    {
        var drives = new[] { Drive("C:\\", 14.5), Drive("D:\\", 60) };
        Assert.True(DiskLevels.Critical(drives).ok);
        var warning = DiskLevels.Warning(drives);
        Assert.False(warning.ok);
        Assert.Contains("C:\\", warning.message);
        Assert.Contains("14,5% livre", warning.message);
        Assert.DoesNotContain("D:\\", warning.message);
    }

    [Fact]
    public void Below_ten_percent_only_the_critical_level_fires_and_attention_steps_aside()
    {
        var drives = new[] { Drive("C:\\", 3.2) };
        var critical = DiskLevels.Critical(drives);
        Assert.False(critical.ok);
        Assert.StartsWith("Pouco espaço livre:", critical.message);
        Assert.Contains("3,2% livre", critical.message);
        Assert.True(DiskLevels.Warning(drives).ok);
    }

    [Fact]
    public void Boundaries_belong_to_the_milder_level()
    {
        Assert.True(DiskLevels.Critical(new[] { Drive("C:\\", 10) }).ok);   // 10% exatos não é crítico
        Assert.False(DiskLevels.Warning(new[] { Drive("C:\\", 10) }).ok);   // ... mas já é atenção
        Assert.True(DiskLevels.Warning(new[] { Drive("C:\\", 20) }).ok);    // 20% exatos ainda é normal
    }

    [Fact]
    public void Two_drives_in_different_levels_report_each_in_its_own_monitor()
    {
        var drives = new[] { Drive("C:\\", 4), Drive("D:\\", 15) };
        Assert.Contains("C:\\", DiskLevels.Critical(drives).message);
        Assert.DoesNotContain("D:\\", DiskLevels.Critical(drives).message);
        Assert.Contains("D:\\", DiskLevels.Warning(drives).message);
        Assert.DoesNotContain("C:\\", DiskLevels.Warning(drives).message);
    }

    [Fact]
    public void An_empty_disk_total_never_divides_by_zero()
    {
        Assert.False(double.IsNaN(new DiskLevels.Drive("X:\\", 0, 0).FreePercent));
    }
}
