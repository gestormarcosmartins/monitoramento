using System.Text.Json;
using Microsoft.Extensions.Options;
using Npgsql;

namespace Nexora.Agent;

public sealed class ChaveouMonitorCollector
{
    private readonly AgentOptions _options;

    public ChaveouMonitorCollector(IOptions<AgentOptions> options) => _options = options.Value;

    public bool Enabled =>
        !string.IsNullOrWhiteSpace(_options.ChaveouConnectionString) &&
        !string.IsNullOrWhiteSpace(_options.ChaveouTenantPublicId);

    private static string? Text(JsonElement element, string name) =>
        element.ValueKind == JsonValueKind.Object &&
        element.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private NpgsqlConnection Connection()
    {
        var builder = new NpgsqlConnectionStringBuilder(_options.ChaveouConnectionString)
        {
            ApplicationName = "Nexora Agent",
            Timeout = Math.Clamp(_options.ChaveouCommandTimeoutSeconds, 5, 120),
            CommandTimeout = Math.Clamp(_options.ChaveouCommandTimeoutSeconds, 5, 300),
        };
        builder.Options = $"{builder.Options} -c default_transaction_read_only=on".Trim();
        return new NpgsqlConnection(builder.ConnectionString);
    }

    private async Task<List<Dictionary<string, object?>>> QueryAsync(string sql, CancellationToken token)
    {
        await using var connection = Connection();
        await connection.OpenAsync(token);
        await using var command = new NpgsqlCommand(sql, connection)
        {
            CommandTimeout = Math.Clamp(_options.ChaveouCommandTimeoutSeconds, 5, 300),
        };
        await using var reader = await command.ExecuteReaderAsync(token);
        var rows = new List<Dictionary<string, object?>>();
        while (await reader.ReadAsync(token))
        {
            var row = new Dictionary<string, object?>(StringComparer.OrdinalIgnoreCase);
            for (var index = 0; index < reader.FieldCount; index++)
                row[reader.GetName(index)] = reader.IsDBNull(index) ? null : reader.GetValue(index);
            rows.Add(row);
        }
        return rows;
    }

    private static Dictionary<string, RemoteMonitor> Monitors(AgentConfiguration config) => config.Monitors
        .Select(m => new { Monitor = m, Code = Text(m.Configuration, "sourceCode") ?? Text(m.Configuration, "code") ?? Text(m.Configuration, "check_id") })
        .Where(x => !string.IsNullOrWhiteSpace(x.Code))
        .ToDictionary(x => x.Code!, x => x.Monitor, StringComparer.OrdinalIgnoreCase);

    public async Task<Dictionary<string, object?>> HealthAsync(CancellationToken token)
    {
        if (!Enabled) return new() { ["enabled"] = false };
        var collectedAt = DateTimeOffset.UtcNow;
        try
        {
            var row = (await QueryAsync(
                "SELECT current_database() database_name, current_user user_name, version() version, pg_postmaster_start_time() started_at, pg_database_size(current_database()) size_bytes",
                token)).First();
            return new()
            {
                ["enabled"] = true, ["ok"] = true, ["collected_at"] = collectedAt,
                ["database"] = row.GetValueOrDefault("database_name"), ["user"] = row.GetValueOrDefault("user_name"),
                ["version"] = row.GetValueOrDefault("version"), ["started_at"] = row.GetValueOrDefault("started_at"),
                ["size_bytes"] = row.GetValueOrDefault("size_bytes"),
            };
        }
        catch (Exception error)
        {
            return new() { ["enabled"] = true, ["ok"] = false, ["collected_at"] = collectedAt, ["error"] = error.Message };
        }
    }

    public async Task<SnapshotEnvelope?> CollectAsync(
        AgentConfiguration config,
        CancellationToken cancellationToken)
    {
        if (!Enabled) return null;

        var monitorByCode = Monitors(config);
        if (monitorByCode.Count == 0) return null;

        await using var connection = Connection();
        await connection.OpenAsync(cancellationToken);
        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        long tenantId;
        await using (var tenantCommand = new NpgsqlCommand(
            "SELECT pk_tenants FROM sy_tenants WHERE public_id=$1 AND ativo AND excluido_em IS NULL",
            connection,
            transaction))
        {
            tenantCommand.Parameters.AddWithValue(Guid.Parse(_options.ChaveouTenantPublicId));
            var value = await tenantCommand.ExecuteScalarAsync(cancellationToken)
                ?? throw new InvalidOperationException("Tenant Chaveou não encontrado ou inativo.");
            tenantId = Convert.ToInt64(value);
        }

        await using (var contextCommand = new NpgsqlCommand(
            "SELECT set_config('app.fk_tenants',$1,true)",
            connection,
            transaction))
        {
            contextCommand.Parameters.AddWithValue(tenantId.ToString());
            await contextCommand.ExecuteNonQueryAsync(cancellationToken);
        }

        var states = new List<ChaveouServiceState>();
        await using (var command = new NpgsqlCommand(
            """
            SELECT codigo,nome,estado,ultimo_resultado_codigo,latencia_ms,ultima_verificacao_em
            FROM mo_services
            WHERE fk_tenants=$1 AND habilitado
            ORDER BY codigo
            """,
            connection,
            transaction))
        {
            command.Parameters.AddWithValue(tenantId);
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
                states.Add(new ChaveouServiceState(
                    reader.GetString(0),
                    reader.GetString(1),
                    reader.GetString(2),
                    reader.IsDBNull(3) ? null : reader.GetString(3),
                    reader.IsDBNull(4) ? null : reader.GetInt32(4),
                    reader.IsDBNull(5) ? null : reader.GetFieldValue<DateTimeOffset>(5)));
        }
        await transaction.RollbackAsync(cancellationToken);

        var collectedAt = DateTimeOffset.UtcNow;
        var results = states
            .Where(state => monitorByCode.ContainsKey(state.Code))
            .Select(state =>
            {
                var status = state.State switch
                {
                    "online" => "up",
                    "desconhecido" => "unknown",
                    _ => "down",
                };
                var ok = status == "up";
                var details = state.ResultCode ?? "SEM_RESULTADO";
                var latency = state.LatencyMs is null ? "" : $" em {state.LatencyMs} ms";
                return new AgentResult(
                    monitorByCode[state.Code].Id,
                    ok,
                    status,
                    $"{state.Name}: {state.State} ({details}){latency}",
                    state.CheckedAt ?? collectedAt);
            })
            .ToList();

        if (results.Count == 0) return null;
        return new SnapshotEnvelope(
            $"chaveou-{collectedAt:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}",
            collectedAt,
            results,
            new Dictionary<string, object?>
            {
                ["source"] = "chaveou-monitor",
                ["tenantPublicId"] = _options.ChaveouTenantPublicId,
                ["services"] = results.Count,
            });
    }

    public async Task<SnapshotEnvelope?> OperationalAsync(AgentConfiguration config, CancellationToken token)
    {
        if (!Enabled) return null;
        var monitors = Monitors(config);
        var results = new List<AgentResult>();
        var now = DateTimeOffset.UtcNow;
        async Task Add(string code, string sql, Func<List<Dictionary<string, object?>>, (bool ok, string message)> evaluate)
        {
            if (!monitors.TryGetValue(code, out var monitor)) return;
            try
            {
                var evaluation = evaluate(await QueryAsync(sql, token));
                results.Add(new(monitor.Id, evaluation.ok, evaluation.ok ? "up" : "down", evaluation.message, now));
            }
            catch (Exception error) { results.Add(new(monitor.Id, false, "unknown", error.Message, now)); }
        }

        await Add("postgres-connectivity", "SELECT current_database() database_name", rows =>
            (true, $"PostgreSQL online: {rows.First().GetValueOrDefault("database_name")}"));
        await Add("postgres-connections", "SELECT (SELECT count(*) FROM pg_stat_activity)::bigint active_connections, current_setting('max_connections')::bigint max_connections", rows =>
        {
            var current = Convert.ToInt64(rows.First()["active_connections"]);
            var maximum = Convert.ToInt64(rows.First()["max_connections"]);
            var percent = maximum == 0 ? 0 : Math.Round(current * 100d / maximum, 1);
            return (percent < 85, $"Conexões: {current}/{maximum} ({percent}%).");
        });
        await Add("postgres-blocking", "SELECT count(*)::bigint blocked FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0", rows =>
        {
            var count = Convert.ToInt64(rows.First()["blocked"]);
            return (count == 0, count == 0 ? "Sem sessões bloqueadas." : $"{count} sessão(ões) bloqueada(s).");
        });
        await Add("postgres-long-transactions", "SELECT count(*)::bigint long_transactions FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND xact_start < clock_timestamp() - interval '15 minutes'", rows =>
        {
            var count = Convert.ToInt64(rows.First()["long_transactions"]);
            return (count == 0, count == 0 ? "Sem transações acima de 15 minutos." : $"{count} transação(ões) acima de 15 minutos.");
        });
        await Add("postgres-vacuum", "SELECT coalesce(max(CASE WHEN n_live_tup=0 THEN 0 ELSE n_dead_tup::numeric/n_live_tup END),0) dead_ratio, coalesce(sum(n_dead_tup),0)::bigint dead_tuples FROM pg_stat_user_tables", rows =>
        {
            var ratio = Convert.ToDouble(rows.First()["dead_ratio"] ?? 0);
            var dead = Convert.ToInt64(rows.First()["dead_tuples"] ?? 0);
            return (ratio < 0.20, $"Tuplas mortas: {dead}; maior proporção por tabela: {ratio:P1}.");
        });
        await Add("postgres-replication", "SELECT count(*)::bigint replicas, coalesce(max(extract(epoch from replay_lag)),0)::double precision max_lag_seconds FROM pg_stat_replication", rows =>
        {
            var replicas = Convert.ToInt64(rows.First()["replicas"]);
            var lag = Convert.ToDouble(rows.First()["max_lag_seconds"] ?? 0);
            return (lag < 60, replicas == 0 ? "Servidor sem réplica configurada." : $"Réplicas: {replicas}; maior atraso: {lag:N1}s.");
        });
        return results.Count == 0 ? null : new(
            $"postgres-health-{now:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}", now, results,
            new() { ["source"] = "postgresql-direct", ["profile"] = "operational_health", ["read_only"] = true });
    }

    public async Task<SnapshotEnvelope?> PerformanceAsync(AgentConfiguration config, CancellationToken token)
    {
        if (!Enabled) return null;
        var monitor = config.Monitors.FirstOrDefault(m => string.Equals(
            Text(m.Configuration, "sourceCode") ?? Text(m.Configuration, "code") ?? Text(m.Configuration, "check_id"),
            "postgres-performance", StringComparison.OrdinalIgnoreCase));
        if (monitor is null) return null;
        var now = DateTimeOffset.UtcNow;
        try
        {
            var extension = Convert.ToBoolean((await QueryAsync("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_stat_statements') enabled", token)).First()["enabled"]);
            var sql = extension
                ? "SELECT calls,total_exec_time,mean_exec_time,rows FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10"
                : "SELECT state,wait_event_type,count(*)::bigint sessions FROM pg_stat_activity WHERE pid<>pg_backend_pid() GROUP BY state,wait_event_type ORDER BY sessions DESC LIMIT 10";
            var rows = await QueryAsync(sql, token);
            var message = extension
                ? $"Análise somente leitura concluída em {rows.Count} consultas de maior tempo acumulado."
                : $"Análise básica concluída em {rows.Count} grupos de sessões; pg_stat_statements não está habilitado.";
            return new($"postgres-performance-{now:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}", now,
                [new(monitor.Id, true, "up", message, now)],
                new() { ["source"] = "postgresql-direct", ["profile"] = "performance", ["read_only"] = true, ["pg_stat_statements"] = extension, ["sample_size"] = rows.Count });
        }
        catch (Exception error)
        {
            return new($"postgres-performance-{now:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}", now,
                [new(monitor.Id, false, "unknown", error.Message, now)],
                new() { ["source"] = "postgresql-direct", ["profile"] = "performance", ["read_only"] = true });
        }
    }
}
