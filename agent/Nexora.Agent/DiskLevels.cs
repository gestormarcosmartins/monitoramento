namespace Nexora.Agent;

/// <summary>Dois níveis para espaço em disco: atenção abaixo de 20% livre e crítico abaixo de 10%. Lógica pura, sem dependências.</summary>
public static class DiskLevels
{
    public const double CriticalBelowPercent = 10;
    public const double WarningBelowPercent = 20;

    public sealed record Drive(string Name, long TotalBytes, long FreeBytes)
    {
        public double FreePercent => Math.Round(FreeBytes * 100d / Math.Max(1, TotalBytes), 1);
    }

    // sem depender da cultura do Windows (nem de globalização invariante): ponto vira vírgula na mão
    private static string Describe(IEnumerable<Drive> drives) =>
        string.Join(", ", drives.Select(d => $"{d.Name} ({d.FreePercent.ToString("0.#", System.Globalization.CultureInfo.InvariantCulture).Replace('.', ',')}% livre)"));

    /// <summary>Crítico: alguma unidade com menos de 10% livre. O texto começa com "Pouco espaço livre:" como sempre.</summary>
    public static (bool ok, string message) Critical(IEnumerable<Drive> drives)
    {
        var low = drives.Where(d => d.FreePercent < CriticalBelowPercent).ToArray();
        return low.Length == 0 ? (true, "Espaço em disco normal em todas as unidades") : (false, $"Pouco espaço livre: {Describe(low)}");
    }

    /// <summary>Atenção: unidades entre 10% e 20% livre. Abaixo de 10% é o nível crítico e este aqui volta a "ok" (um aviso por vez).</summary>
    public static (bool ok, string message) Warning(IEnumerable<Drive> drives)
    {
        var attention = drives.Where(d => d.FreePercent >= CriticalBelowPercent && d.FreePercent < WarningBelowPercent).ToArray();
        return attention.Length == 0 ? (true, "Nenhuma unidade na faixa de atenção (10% a 20% livre)") : (false, $"Espaço livre abaixo de 20%: {Describe(attention)}");
    }
}
