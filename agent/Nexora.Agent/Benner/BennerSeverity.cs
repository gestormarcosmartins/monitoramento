namespace Nexora.Agent.Benner;

// Per-check severity classification, attention-point extraction, dashboard-metric
// extraction and backup-summary derivation - ported verbatim from Python's
// severity_for() / attention_points() / dashboard_metrics() / backup_from_items()
// (app.py lines ~856-1033, 234-254). Keep thresholds/column names/rounding identical;
// these numbers are what decides whether a check is "ok"/"warning"/"critical".
public static class BennerSeverity
{
    private static double? AsDouble(object? value)
    {
        if (value is null || value is DBNull) return null;
        try
        {
            return value switch
            {
                double d => d,
                float f => f,
                decimal m => (double)m,
                int i => i,
                long l => l,
                short s => s,
                byte b => b,
                bool bo => bo ? 1 : 0,
                string str => double.TryParse(str, System.Globalization.CultureInfo.InvariantCulture, out var parsed) ? parsed : null,
                _ => Convert.ToDouble(value, System.Globalization.CultureInfo.InvariantCulture),
            };
        }
        catch { return null; }
    }

    /// <summary>Python's _worst_row_value(rows, column, lowest=False): the max (or min when
    /// lowest=true) numeric value of `column` across all rows, ignoring nulls/unparseable
    /// values; null if no row has a usable value.</summary>
    private static double? WorstRowValue(List<Dictionary<string, object?>> rows, string column, bool lowest = false)
    {
        var values = new List<double>();
        foreach (var row in rows)
        {
            if (!row.TryGetValue(column, out var raw)) continue;
            var value = AsDouble(raw);
            if (value is not null) values.Add(value.Value);
        }
        if (values.Count == 0) return null;
        return lowest ? values.Min() : values.Max();
    }

    public static string SeverityFor(int checkId, List<List<Dictionary<string, object?>>> resultSets, BennerThresholds limits)
    {
        try
        {
            var rows = resultSets.Count > 0 ? resultSets[0] : new List<Dictionary<string, object?>>();
            if (checkId == 2)
                return rows.Count > 0 ? "critical" : "ok";
            if (checkId is 4 or 5)
            {
                var worst = 0.0;
                foreach (var row in rows)
                    foreach (var key in new[] { "latencia_total_ms", "read_latency_ms", "write_latency_ms", "latencia_leitura_ms", "latencia_escrita_ms" })
                        if (row.TryGetValue(key, out var raw) && AsDouble(raw) is { } value)
                            worst = Math.Max(worst, value);
                if (worst >= 50) return "critical";
                if (worst >= 20) return "warning";
                return "ok";
            }
            if (checkId == 10)
            {
                var free = WorstRowValue(rows, "available_memory_mb", lowest: true);
                if (free is null) return "info";
                if (free < limits.MemAvailableCritMb) return "critical";
                if (free < limits.MemAvailableWarnMb) return "warning";
                return "ok";
            }
            if (checkId == 16)
            {
                var free = WorstRowValue(rows, "livre_pct", lowest: true);
                if (free is null) return "info";
                if (free < limits.DiskFreeCritPct) return "critical";
                if (free < limits.DiskFreeWarnPct) return "warning";
                return "ok";
            }
            if (checkId == 17)
            {
                var version = WorstRowValue(rows, "version_store_gb") ?? 0;
                var used = WorstRowValue(rows, "usado_pct") ?? 0;
                if (version >= limits.TempdbVersionCritGb) return "critical";
                if (version >= limits.TempdbVersionWarnGb || used >= limits.TempdbUsedWarnPct) return "warning";
                return "ok";
            }
            if (checkId == 19)
            {
                var waited = WorstRowValue(rows, "maior_espera_s") ?? 0;
                if (waited >= limits.BlockCritSeconds) return "critical";
                if (waited >= limits.BlockWarnSeconds) return "warning";
                return "ok";
            }
            if (checkId == 18)
            {
                var longest = WorstRowValue(rows, "minutos_aberta") ?? 0;
                if (longest >= limits.LongTranCritMinutes) return "critical";
                if (rows.Count > 0) return "warning";
                return "ok";
            }
            if (checkId == 15)
                return rows.Any(r => Equals(r.GetValueOrDefault("full_status"), "NOK") || Equals(r.GetValueOrDefault("log_status"), "NOK"))
                    ? "critical" : "ok";
            if (checkId == 12)
            {
                var worst = 0.0;
                foreach (var row in rows)
                    foreach (var (key, raw) in row)
                        if (key.Contains("Log Space Used") && AsDouble(raw) is { } value)
                            worst = Math.Max(worst, value);
                if (worst >= 90) return "critical";
                if (worst >= 75) return "warning";
                return "ok";
            }
            return "info";
        }
        catch
        {
            return "info";
        }
    }

    private static readonly string[] LatencyColumns =
        { "latencia_total_ms", "read_latency_ms", "write_latency_ms", "latencia_leitura_ms", "latencia_escrita_ms" };

    public static List<BennerAttentionPoint> AttentionPoints(List<BennerItem> items)
    {
        var points = new List<BennerAttentionPoint>();
        foreach (var item in items)
        {
            if (item.Status != "success" || item.Severity is not ("warning" or "critical")) continue;
            var check = item.Check;
            var rows = item.ResultSets.Count > 0 ? item.ResultSets[0] : new List<Dictionary<string, object?>>();
            var point = new BennerAttentionPoint
            {
                CheckId = check.Id, Name = check.Name, Severity = item.Severity,
                Metric = "registros", Value = rows.Count, Unit = "registro(s)",
            };
            switch (check.Id)
            {
                case 2:
                    point.Metric = "sessões bloqueadas"; point.Value = rows.Count; point.Unit = "sessão(ões)";
                    break;
                case 4 or 5:
                    var values = new List<double>();
                    foreach (var row in rows)
                        foreach (var column in LatencyColumns)
                            if (row.TryGetValue(column, out var raw) && AsDouble(raw) is { } value)
                                values.Add(value);
                    if (values.Count > 0)
                    {
                        point.Metric = "pior latência"; point.Value = Math.Round(values.Max(), 2); point.Unit = "ms";
                    }
                    break;
                case 15:
                    var failing = rows.Count(r => Equals(r.GetValueOrDefault("full_status"), "NOK") || Equals(r.GetValueOrDefault("log_status"), "NOK"));
                    point.Metric = "bancos com falha de backup"; point.Value = failing; point.Unit = "banco(s)";
                    break;
                case 10:
                    point.Metric = "memória disponível no Windows"; point.Value = WorstRowValue(rows, "available_memory_mb", lowest: true);
                    point.Unit = "MB"; point.LowerIsWorse = true;
                    break;
                case 16:
                {
                    var low = rows.Count == 0 ? null : rows.MinBy(r => AsDouble(r.GetValueOrDefault("livre_pct")) ?? 100);
                    var volume = low?.GetValueOrDefault("volume")?.ToString() ?? "?";
                    point.Metric = $"menor espaço livre ({volume})"; point.Value = WorstRowValue(rows, "livre_pct", lowest: true);
                    point.Unit = "%"; point.LowerIsWorse = true;
                    break;
                }
                case 17:
                    point.Metric = "version store no tempdb"; point.Value = WorstRowValue(rows, "version_store_gb"); point.Unit = "GB";
                    break;
                case 18:
                    point.Metric = "maior transação aberta"; point.Value = WorstRowValue(rows, "minutos_aberta"); point.Unit = "min";
                    break;
                case 19:
                {
                    var top = rows.Count == 0 ? null : rows.MaxBy(r => AsDouble(r.GetValueOrDefault("maior_espera_s")) ?? 0);
                    point.Metric = "maior espera por bloqueio"; point.Value = WorstRowValue(rows, "maior_espera_s"); point.Unit = "s";
                    point.Detail = $"raiz sessão {top?.GetValueOrDefault("sessao_raiz")} ({top?.GetValueOrDefault("host_name") ?? "?"} / {top?.GetValueOrDefault("program_name") ?? "?"}), {top?.GetValueOrDefault("sessoes_bloqueadas")} bloqueada(s)";
                    break;
                }
                case 12:
                    var logValues = new List<double>();
                    foreach (var row in rows)
                        foreach (var (key, raw) in row)
                            if (key.Contains("Log Space Used") && AsDouble(raw) is { } value)
                                logValues.Add(value);
                    if (logValues.Count > 0)
                    {
                        point.Metric = "uso máximo do log"; point.Value = Math.Round(logValues.Max(), 2); point.Unit = "%";
                    }
                    break;
            }
            points.Add(point);
        }
        return points;
    }

    private sealed record Specification(string Label, string Unit, string[]? Columns, bool HigherIsWorse);

    private static readonly Dictionary<int, Specification> Specifications = new()
    {
        [1] = new("requests ativos", "registro(s)", null, true),
        [2] = new("sessões bloqueadas", "sessão(ões)", null, true),
        [3] = new("maior espera", "ms", new[] { "wait_time_ms" }, true),
        [4] = new("pior latência", "ms", new[] { "latencia_total_ms", "latencia_leitura_ms", "latencia_escrita_ms" }, true),
        [5] = new("pior latência Benner", "ms", new[] { "read_latency_ms", "write_latency_ms" }, true),
        [6] = new("maior CPU acumulada", "ms", new[] { "total_cpu_ms" }, true),
        [7] = new("maiores leituras", "leituras", new[] { "total_logical_reads" }, true),
        [8] = new("maior tempo médio", "ms", new[] { "tempo_medio_ms" }, true),
        [9] = new("memória SQL", "MB", new[] { "sql_memory_mb" }, true),
        [10] = new("memória disponível", "MB", new[] { "available_memory_mb" }, false),
        [11] = new("maior arquivo", "MB", new[] { "tamanho_mb" }, true),
        [12] = new("uso máximo do log", "%", new[] { "Log Space Used (%)" }, true),
        [13] = new("sessões ativas", "sessão(ões)", new[] { "sessoes" }, true),
        [14] = new("requests ativos", "registro(s)", null, true),
        [15] = new("bancos com falha de backup", "banco(s)", null, true),
        [16] = new("menor espaço livre", "%", new[] { "livre_pct" }, false),
        [17] = new("version store no tempdb", "GB", new[] { "version_store_gb" }, true),
        [18] = new("maior transação aberta", "min", new[] { "minutos_aberta" }, true),
        [19] = new("maior espera por bloqueio", "s", new[] { "maior_espera_s" }, true),
    };

    public static List<BennerMetric> DashboardMetrics(List<BennerItem> items)
    {
        var metrics = new List<BennerMetric>();
        foreach (var item in items)
        {
            var check = item.Check;
            var spec = Specifications[check.Id];
            var metric = new BennerMetric
            {
                CheckId = check.Id, Name = check.Name, Label = spec.Label, Unit = spec.Unit,
                HigherIsWorse = spec.HigherIsWorse, Value = null, Status = item.Status,
            };
            var rows = item.ResultSets.Count > 0 ? item.ResultSets[0] : new List<Dictionary<string, object?>>();
            if (item.Status == "success")
            {
                if (check.Id == 15)
                    metric.Value = rows.Count(r => Equals(r.GetValueOrDefault("full_status"), "NOK") || Equals(r.GetValueOrDefault("log_status"), "NOK"));
                else if (check.Id == 16)
                    metric.Value = WorstRowValue(rows, "livre_pct", lowest: true);
                else if (check.Id == 18)
                    metric.Value = WorstRowValue(rows, "minutos_aberta") ?? 0;
                else if (check.Id == 19)
                    metric.Value = WorstRowValue(rows, "maior_espera_s") ?? 0;
                else if (spec.Columns is null)
                    metric.Value = rows.Count;
                else
                {
                    var values = new List<double>();
                    foreach (var row in rows)
                        foreach (var column in spec.Columns)
                            if (row.TryGetValue(column, out var raw) && AsDouble(raw) is { } value)
                                values.Add(value);
                    if (values.Count > 0)
                        metric.Value = Math.Round(values.Max(), 2);
                    else if (check.Id == 10 && rows.Count > 0 && AsDouble(rows[0].GetValueOrDefault("available_memory_mb")) is { } available)
                        metric.Value = Math.Round(available, 2);
                }
            }
            metrics.Add(metric);
        }
        return metrics;
    }

    private static string? StringifyIfDate(string key, object? value)
    {
        if (value is null) return null;
        if ((key == "ultimo_full" || key == "ultimo_log") && value is DateTime dt)
            return dt.ToString("yyyy-MM-dd HH:mm:ss");
        return null;
    }

    /// <summary>Python's backup_summary(rows): FULL/LOGS OK/NOK blocks plus the raw per-database
    /// rows (with ultimo_full/ultimo_log stringified), used only when check 15's own severity
    /// already evaluated its rows - this just reshapes them for the dedicated "backup" field.</summary>
    private static BennerBackupSummary BackupSummary(List<Dictionary<string, object?>> rows, BennerThresholds thresholds)
    {
        BennerBackupBlock Block(string key, string label)
        {
            var applicable = rows.Where(r => Equals(r.GetValueOrDefault(key), "OK") || Equals(r.GetValueOrDefault(key), "NOK")).ToList();
            var failing = applicable.Where(r => Equals(r.GetValueOrDefault(key), "NOK")).Select(r => r.GetValueOrDefault("banco")?.ToString() ?? "").ToList();
            return new BennerBackupBlock
            {
                Label = label,
                Status = failing.Count > 0 ? "NOK" : (applicable.Count > 0 ? "OK" : "N/A"),
                Ok = applicable.Count - failing.Count, Total = applicable.Count, Failing = failing,
            };
        }
        var databases = rows.Select(r => r.ToDictionary(kv => kv.Key, kv => (object?)(StringifyIfDate(kv.Key, kv.Value) ?? kv.Value))).ToList();
        return new BennerBackupSummary
        {
            Full = Block("full_status", "FULL"),
            Logs = Block("log_status", "LOGS"),
            Thresholds = new BennerBackupThresholds
            {
                FullMaxAgeHours = thresholds.BackupFullMaxAgeHours,
                LogMaxGapMinutes = thresholds.BackupLogMaxGapMinutes,
                LogLookbackHours = thresholds.BackupLogLookbackHours,
            },
            Databases = databases,
        };
    }

    public static BennerBackupSummary? BackupFromItems(List<BennerItem> items, BennerThresholds thresholds)
    {
        var item = items.FirstOrDefault(i => i.Check.Id == 15);
        if (item is null) return null;
        if (item.Status != "success")
            return new BennerBackupSummary { Error = item.Error ?? "falha na consulta de backup" };
        var rows = item.ResultSets.Count > 0 ? item.ResultSets[0] : new List<Dictionary<string, object?>>();
        return BackupSummary(rows, thresholds);
    }
}
