namespace Nexora.Agent.Benner;

// The 19 SQL Server health checks ported verbatim (SQL text, ids, names, categories,
// descriptions) from the legacy Python "benner_health_monitor" (app.py CHECKS list,
// lines ~530-807, plus the io_latency_sql/backup_sql/long_tran_sql builders they call).
// Do not "clean up" or reformat the SQL - fidelity with the Python version is a hard
// requirement so the resulting payload shape/semantics match what Nexora already expects.
public static class BennerCheckDefinitions
{
    // Checks 4 and 5 share this builder (io_latency_sql(only_benner) in Python). It takes two
    // samples of sys.dm_io_virtual_file_stats a few seconds apart (WAITFOR DELAY) and computes
    // windowed read/write latency, zeroing rows whose op count in the window is below the
    // configured minimum to avoid false alarms from tiny samples.
    private static string IoLatencySql(bool onlyBenner, BennerThresholds thresholds)
    {
        var seconds = thresholds.EffectiveLatencySampleSeconds;
        var minOps = Math.Max(1, thresholds.IoLatencyMinOps);
        var delay = $"00:00:{seconds:D2}";
        var columns = onlyBenner
            ? $"""
                  DB_NAME(b.database_id) AS banco,
                  mf.type_desc,
                  mf.physical_name,
                  CAST(CASE WHEN d.reads < {minOps} THEN 0 ELSE 1.0 * d.read_stall / d.reads END AS DECIMAL(10,2)) AS read_latency_ms,
                  CAST(CASE WHEN d.writes < {minOps} THEN 0 ELSE 1.0 * d.write_stall / d.writes END AS DECIMAL(10,2)) AS write_latency_ms,
                  d.reads AS reads_janela,
                  d.writes AS writes_janela,
                  CAST(CASE WHEN b.num_of_reads = 0 THEN 0 ELSE 1.0 * b.io_stall_read_ms / b.num_of_reads END AS DECIMAL(10,2)) AS read_acumulada_ms,
                  CAST(CASE WHEN b.num_of_writes = 0 THEN 0 ELSE 1.0 * b.io_stall_write_ms / b.num_of_writes END AS DECIMAL(10,2)) AS write_acumulada_ms,
                  b.num_of_reads,
                  b.num_of_writes
              """
            : $"""
                  DB_NAME(b.database_id) AS banco,
                  mf.name AS arquivo_logico,
                  mf.type_desc,
                  mf.physical_name,
                  CAST(b.size_on_disk_bytes / 1024.0 / 1024 / 1024 AS DECIMAL(18,2)) AS tamanho_gb,
                  CAST(CASE WHEN d.reads < {minOps} THEN 0 ELSE 1.0 * d.read_stall / d.reads END AS DECIMAL(10,2)) AS latencia_leitura_ms,
                  CAST(CASE WHEN d.writes < {minOps} THEN 0 ELSE 1.0 * d.write_stall / d.writes END AS DECIMAL(10,2)) AS latencia_escrita_ms,
                  CAST(CASE WHEN d.reads + d.writes < {minOps} THEN 0
                      ELSE 1.0 * (d.read_stall + d.write_stall) / (d.reads + d.writes) END AS DECIMAL(10,2)) AS latencia_total_ms,
                  d.reads AS reads_janela,
                  d.writes AS writes_janela,
                  CAST(CASE WHEN (b.num_of_reads + b.num_of_writes) = 0 THEN 0
                      ELSE 1.0 * b.io_stall / (b.num_of_reads + b.num_of_writes) END AS DECIMAL(10,2)) AS latencia_acumulada_ms,
                  b.num_of_reads,
                  b.num_of_writes
              """;
        var where = onlyBenner ? "WHERE DB_NAME(b.database_id) LIKE '%BENNER%'" : "";
        var order = onlyBenner
            ? "ORDER BY d.read_stall + d.write_stall DESC, b.num_of_reads DESC;"
            : "ORDER BY latencia_total_ms DESC;";
        return $"""
            SET NOCOUNT ON;
            DECLARE @antes TABLE (database_id INT, file_id INT, reads BIGINT, read_stall BIGINT, writes BIGINT, write_stall BIGINT);
            INSERT INTO @antes
            SELECT database_id, file_id, num_of_reads, io_stall_read_ms, num_of_writes, io_stall_write_ms
            FROM sys.dm_io_virtual_file_stats(NULL, NULL);
            WAITFOR DELAY '{delay}';
            SELECT
            {columns}
            FROM sys.dm_io_virtual_file_stats(NULL, NULL) b
            INNER JOIN sys.master_files mf ON mf.database_id = b.database_id AND mf.file_id = b.file_id
            INNER JOIN @antes a ON a.database_id = b.database_id AND a.file_id = b.file_id
            CROSS APPLY (SELECT
                b.num_of_reads - a.reads AS reads, b.io_stall_read_ms - a.read_stall AS read_stall,
                b.num_of_writes - a.writes AS writes, b.io_stall_write_ms - a.write_stall AS write_stall) d
            {where}
            {order}
            """;
    }

    // Check 15 (backup_sql() in Python): literal textual substitution of thresholds into the
    // template - NOT SQL parameters, matching the Python implementation exactly.
    private const string BackupSqlTemplate = """
        DECLARE @full_h INT = __FULL_H__, @gap_min INT = __GAP_MIN__;
        DECLARE @agora DATETIME = GETDATE(), @corte DATETIME = DATEADD(HOUR, -__LOOK_H__, GETDATE());

        WITH fulls AS (
            SELECT database_name, MAX(backup_finish_date) AS ultimo_full
            FROM msdb.dbo.backupset
            WHERE type = 'D' AND server_name = @@SERVERNAME
            GROUP BY database_name
        ),
        logs AS (
            SELECT database_name, backup_finish_date, first_lsn,
                LAG(backup_finish_date) OVER (PARTITION BY database_name ORDER BY last_lsn) AS fim_anterior,
                LAG(last_lsn) OVER (PARTITION BY database_name ORDER BY last_lsn) AS lsn_anterior
            FROM msdb.dbo.backupset
            WHERE type = 'L' AND is_copy_only = 0 AND server_name = @@SERVERNAME
              AND backup_finish_date >= DATEADD(HOUR, -(__LOOK_H__ + 24), GETDATE())
        ),
        logstat AS (
            SELECT l.database_name,
                MAX(l.backup_finish_date) AS ultimo_log,
                SUM(CASE WHEN l.backup_finish_date >= @corte THEN 1 ELSE 0 END) AS logs_periodo,
                MAX(CASE WHEN l.backup_finish_date >= @corte AND l.fim_anterior IS NOT NULL
                         THEN DATEDIFF(MINUTE, l.fim_anterior, l.backup_finish_date) END) AS maior_gap_min,
                SUM(CASE WHEN l.backup_finish_date >= @corte AND l.lsn_anterior IS NOT NULL
                         AND l.first_lsn <> l.lsn_anterior
                         AND (f.ultimo_full IS NULL OR l.backup_finish_date > f.ultimo_full) THEN 1 ELSE 0 END) AS quebras_cadeia,
                SUM(CASE WHEN l.backup_finish_date >= @corte AND l.lsn_anterior IS NOT NULL
                         AND l.first_lsn <> l.lsn_anterior
                         AND f.ultimo_full IS NOT NULL AND l.backup_finish_date <= f.ultimo_full THEN 1 ELSE 0 END) AS quebras_refeitas
            FROM logs l
            LEFT JOIN fulls f ON f.database_name = l.database_name
            GROUP BY l.database_name
        ),
        base AS (
            SELECT d.name AS banco, d.recovery_model_desc AS recovery_model, d.state_desc AS estado,
                f.ultimo_full, l.ultimo_log, ISNULL(l.logs_periodo, 0) AS logs_periodo,
                l.maior_gap_min, ISNULL(l.quebras_cadeia, 0) AS quebras_cadeia, ISNULL(l.quebras_refeitas, 0) AS quebras_refeitas,
                DATEDIFF(MINUTE, f.ultimo_full, @agora) / 60.0 AS horas_desde_full,
                DATEDIFF(MINUTE, l.ultimo_log, @agora) AS minutos_desde_log
            FROM sys.databases d
            LEFT JOIN fulls f ON f.database_name = d.name
            LEFT JOIN logstat l ON l.database_name = d.name
            WHERE d.database_id <> 2 AND d.source_database_id IS NULL
        ),
        calc AS (
            SELECT *,
                CASE WHEN estado <> 'ONLINE' THEN 'N/A'
                     WHEN ultimo_full IS NULL OR horas_desde_full > @full_h THEN 'NOK' ELSE 'OK' END AS full_status,
                CASE WHEN estado <> 'ONLINE' OR recovery_model = 'SIMPLE' THEN 'N/A'
                     WHEN ultimo_log IS NULL OR minutos_desde_log > @gap_min
                          OR ISNULL(maior_gap_min, 0) > @gap_min OR quebras_cadeia > 0 THEN 'NOK' ELSE 'OK' END AS log_status
            FROM base
        )
        SELECT banco, recovery_model, estado,
            ultimo_full, CAST(horas_desde_full AS DECIMAL(10,1)) AS horas_desde_full, full_status,
            ultimo_log, minutos_desde_log, maior_gap_min, quebras_cadeia, quebras_refeitas, logs_periodo, log_status,
            CASE WHEN estado <> 'ONLINE' THEN 'Banco ' + LOWER(estado) + ' (ignorado)'
                 ELSE NULLIF(LTRIM(
                    CASE WHEN full_status = 'NOK' THEN
                         CASE WHEN ultimo_full IS NULL THEN 'Sem backup FULL registrado. '
                              ELSE 'FULL há ' + CAST(CAST(horas_desde_full AS INT) AS VARCHAR(10)) + ' h (limite ' + CAST(@full_h AS VARCHAR(10)) + ' h). ' END
                         ELSE '' END +
                    CASE WHEN log_status = 'NOK' THEN
                         CASE WHEN ultimo_log IS NULL THEN 'Sem backup de LOG registrado. '
                              WHEN minutos_desde_log > @gap_min THEN 'Último LOG há ' + CAST(minutos_desde_log AS VARCHAR(10)) + ' min (limite ' + CAST(@gap_min AS VARCHAR(10)) + ' min). '
                              ELSE '' END +
                         CASE WHEN ISNULL(maior_gap_min, 0) > @gap_min THEN 'Janela de LOG de ' + CAST(maior_gap_min AS VARCHAR(10)) + ' min. ' ELSE '' END +
                         CASE WHEN quebras_cadeia > 0 THEN 'Cadeia de LOG quebrada (' + CAST(quebras_cadeia AS VARCHAR(10)) + ' ruptura(s) de LSN). ' ELSE '' END
                         ELSE '' END +
                    CASE WHEN quebras_refeitas > 0 AND quebras_cadeia = 0 THEN 'Info: ruptura de LSN anterior ao último FULL (cadeia refeita). ' ELSE '' END), '') END AS motivo
        FROM calc
        ORDER BY CASE WHEN full_status = 'NOK' OR log_status = 'NOK' THEN 0 ELSE 1 END, banco;
        """;

    private static string BackupSql(BennerThresholds thresholds) => BackupSqlTemplate
        .Replace("__FULL_H__", thresholds.BackupFullMaxAgeHours.ToString())
        .Replace("__GAP_MIN__", thresholds.BackupLogMaxGapMinutes.ToString())
        .Replace("__LOOK_H__", thresholds.BackupLogLookbackHours.ToString());

    private const string DiskSql = """
        SET NOCOUNT ON;
        SELECT DISTINCT
            vs.volume_mount_point AS volume,
            CAST(vs.total_bytes / 1073741824.0 AS DECIMAL(10,1)) AS total_gb,
            CAST(vs.available_bytes / 1073741824.0 AS DECIMAL(10,1)) AS livre_gb,
            CAST(100.0 * vs.available_bytes / NULLIF(vs.total_bytes, 0) AS DECIMAL(5,1)) AS livre_pct
        FROM sys.master_files mf
        CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) vs
        ORDER BY livre_pct;
        """;

    private const string TempdbSql = """
        SET NOCOUNT ON;
        EXEC tempdb.sys.sp_executesql N'
        SELECT
            CAST(SUM(total_page_count) * 8.0 / 1048576 AS DECIMAL(10,2)) AS tamanho_gb,
            CAST(100.0 * (SUM(total_page_count) - SUM(unallocated_extent_page_count))
                / NULLIF(SUM(total_page_count), 0) AS DECIMAL(5,1)) AS usado_pct,
            CAST(SUM(version_store_reserved_page_count) * 8.0 / 1048576 AS DECIMAL(10,2)) AS version_store_gb,
            CAST(SUM(user_object_reserved_page_count) * 8.0 / 1048576 AS DECIMAL(10,2)) AS objetos_usuario_gb,
            CAST(SUM(internal_object_reserved_page_count) * 8.0 / 1048576 AS DECIMAL(10,2)) AS objetos_internos_gb
        FROM sys.dm_db_file_space_usage;';
        """;

    private const string LongTranSqlTemplate = """
        SET NOCOUNT ON;
        SELECT
            st.session_id,
            DB_NAME(dt.database_id) AS banco,
            s.login_name,
            s.host_name,
            s.program_name,
            s.status,
            at.transaction_begin_time AS inicio,
            DATEDIFF(MINUTE, at.transaction_begin_time, GETDATE()) AS minutos_aberta,
            CAST(SUM(dt.database_transaction_log_bytes_used) / 1048576.0 AS DECIMAL(12,1)) AS log_mb,
            CAST(LEFT(t.text, 200) AS NVARCHAR(200)) AS ultimo_sql
        FROM sys.dm_tran_session_transactions st
        INNER JOIN sys.dm_tran_active_transactions at ON at.transaction_id = st.transaction_id
        INNER JOIN sys.dm_exec_sessions s ON s.session_id = st.session_id AND s.is_user_process = 1
        LEFT JOIN sys.dm_tran_database_transactions dt ON dt.transaction_id = st.transaction_id
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = st.session_id
        OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) t
        WHERE st.session_id <> @@SPID
          AND DATEDIFF(MINUTE, at.transaction_begin_time, GETDATE()) >= __MIN__
        GROUP BY st.session_id, dt.database_id, s.login_name, s.host_name, s.program_name, s.status,
            at.transaction_begin_time, CAST(LEFT(t.text, 200) AS NVARCHAR(200))
        ORDER BY minutos_aberta DESC;
        """;

    private static string LongTranSql(BennerThresholds thresholds) =>
        LongTranSqlTemplate.Replace("__MIN__", thresholds.LongTranWarnMinutes.ToString());

    // Check 19 (BLOCKING_SQL in Python): a constant - NOT dynamically templated despite
    // its description text literally quoting the default warn/crit thresholds (5 s / 30 s);
    // ported verbatim, including that cosmetic staleness-if-env-changed quirk.
    public const string BlockingSql = """
        SET NOCOUNT ON;
        WITH espera AS (
            SELECT r.session_id, r.blocking_session_id, r.wait_time, r.wait_type, r.wait_resource, r.database_id
            FROM sys.dm_exec_requests r
            WHERE r.blocking_session_id > 0 AND r.session_id <> r.blocking_session_id
        ),
        cadeia AS (
            SELECT e.session_id AS bloqueada, e.blocking_session_id AS bloqueador, e.wait_time, 1 AS nivel
            FROM espera e
            UNION ALL
            SELECT c.bloqueada, e.blocking_session_id, c.wait_time, c.nivel + 1
            FROM cadeia c
            INNER JOIN espera e ON e.session_id = c.bloqueador
            WHERE c.nivel < 20
        ),
        raizes AS (
            SELECT c.bloqueada, c.bloqueador AS raiz, c.wait_time
            FROM cadeia c
            WHERE NOT EXISTS (SELECT 1 FROM espera e WHERE e.session_id = c.bloqueador)
        ),
        agg AS (
            SELECT raiz, COUNT(DISTINCT bloqueada) AS sessoes_bloqueadas, MAX(wait_time) AS maior_espera_ms
            FROM raizes
            GROUP BY raiz
        )
        SELECT TOP 20
            a.raiz AS sessao_raiz,
            a.sessoes_bloqueadas,
            CAST(a.maior_espera_ms / 1000.0 AS DECIMAL(10,1)) AS maior_espera_s,
            s.login_name,
            s.host_name,
            s.program_name,
            s.status AS status_sessao,
            s.open_transaction_count AS transacoes_abertas,
            tx.inicio AS transacao_inicio,
            DATEDIFF(SECOND, tx.inicio, GETDATE()) AS transacao_seg,
            CAST(ISNULL(tx.log_bytes, 0) / 1048576.0 AS DECIMAL(12,1)) AS log_mb,
            lk.locks_mantidos,
            rr.command AS comando_atual,
            DB_NAME(ISNULL(rr.database_id, s.database_id)) AS banco,
            CAST(LEFT(COALESCE(SUBSTRING(st.text, (rr.statement_start_offset / 2) + 1, 400), ct.text), 400) AS NVARCHAR(400)) AS sql_raiz,
            pw.wait_type,
            pw.wait_resource,
            CAST(LEFT(wt.text, 300) AS NVARCHAR(300)) AS sql_bloqueada
        FROM agg a
        LEFT JOIN sys.dm_exec_sessions s ON s.session_id = a.raiz
        LEFT JOIN sys.dm_exec_requests rr ON rr.session_id = a.raiz
        LEFT JOIN sys.dm_exec_connections c ON c.session_id = a.raiz
        OUTER APPLY sys.dm_exec_sql_text(rr.sql_handle) st
        OUTER APPLY sys.dm_exec_sql_text(c.most_recent_sql_handle) ct
        OUTER APPLY (
            SELECT MIN(at.transaction_begin_time) AS inicio, SUM(dt.database_transaction_log_bytes_used) AS log_bytes
            FROM sys.dm_tran_session_transactions stx
            INNER JOIN sys.dm_tran_active_transactions at ON at.transaction_id = stx.transaction_id
            LEFT JOIN sys.dm_tran_database_transactions dt ON dt.transaction_id = stx.transaction_id
            WHERE stx.session_id = a.raiz
        ) tx
        OUTER APPLY (
            SELECT COUNT(*) AS locks_mantidos
            FROM sys.dm_tran_locks l
            WHERE l.request_session_id = a.raiz AND l.request_status = 'GRANT'
        ) lk
        OUTER APPLY (
            SELECT TOP 1 e.session_id, e.wait_type, e.wait_resource
            FROM raizes z
            INNER JOIN espera e ON e.session_id = z.bloqueada
            WHERE z.raiz = a.raiz
            ORDER BY z.wait_time DESC
        ) pw
        LEFT JOIN sys.dm_exec_requests rw ON rw.session_id = pw.session_id
        OUTER APPLY sys.dm_exec_sql_text(rw.sql_handle) wt
        ORDER BY a.maior_espera_ms DESC;
        """;

    // Not one of the 19 checks - Python runs this as a 4th query exclusively for the local
    // "attention_queries_indices" log file (write_attention_log()), scoped to whatever database
    // the connection is currently pointed at (DB_ID()). Kept here so BennerSqlServerDiagnostics
    // can reproduce that same local-only artifact alongside the 19-check payload.
    public const string MissingIndexSql = """
        SELECT TOP 20
            DB_NAME(mid.database_id) AS banco,
            mid.statement AS tabela,
            migs.user_seeks,
            migs.user_scans,
            migs.avg_total_user_cost,
            migs.avg_user_impact,
            CAST(migs.avg_total_user_cost * migs.avg_user_impact *
                (migs.user_seeks + migs.user_scans) AS DECIMAL(18, 2)) AS beneficio_estimado,
            mid.equality_columns,
            mid.inequality_columns,
            mid.included_columns
        FROM sys.dm_db_missing_index_group_stats AS migs
        INNER JOIN sys.dm_db_missing_index_groups AS mig
            ON migs.group_handle = mig.index_group_handle
        INNER JOIN sys.dm_db_missing_index_details AS mid
            ON mig.index_handle = mid.index_handle
        WHERE mid.database_id = DB_ID()
        ORDER BY beneficio_estimado DESC;
        """;

    public static IReadOnlyList<BennerCheckDefinition> All { get; } = new List<BennerCheckDefinition>
    {
        new()
        {
            Id = 1, Name = "Execuções atuais", Category = "Sessões",
            Description = "Queries em execução, tempo, CPU, leituras, escritas e waits.",
            StaticSql = """
                SELECT
                    r.session_id,
                    r.status,
                    r.command,
                    DB_NAME(r.database_id) AS banco,
                    r.cpu_time AS cpu_ms,
                    r.total_elapsed_time AS elapsed_ms,
                    r.reads,
                    r.writes,
                    r.logical_reads,
                    r.wait_type,
                    r.wait_time,
                    r.wait_resource,
                    s.login_name,
                    s.host_name,
                    s.program_name,
                    SUBSTRING(
                        t.text,
                        (r.statement_start_offset / 2) + 1,
                        ((CASE r.statement_end_offset
                            WHEN -1 THEN DATALENGTH(t.text)
                            ELSE r.statement_end_offset
                        END - r.statement_start_offset) / 2) + 1
                    ) AS comando_atual
                FROM sys.dm_exec_requests r
                INNER JOIN sys.dm_exec_sessions s
                    ON r.session_id = s.session_id
                CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
                WHERE r.session_id <> @@SPID
                ORDER BY r.total_elapsed_time DESC;
                """
        },
        new()
        {
            Id = 2, Name = "Bloqueios", Category = "Locks",
            Description = "Sessões bloqueadas e respectivo bloqueador.",
            StaticSql = """
                SELECT
                    r.session_id,
                    r.blocking_session_id,
                    r.status,
                    r.wait_type,
                    r.wait_time,
                    r.wait_resource,
                    DB_NAME(r.database_id) AS banco,
                    s.login_name,
                    s.host_name,
                    t.text AS comando
                FROM sys.dm_exec_requests r
                INNER JOIN sys.dm_exec_sessions s
                    ON r.session_id = s.session_id
                CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
                WHERE r.blocking_session_id <> 0
                ORDER BY r.wait_time DESC;
                """
        },
        new()
        {
            Id = 3, Name = "Wait Stats", Category = "SQL Server",
            Description = "Principais esperas acumuladas do SQL Server.",
            StaticSql = """
                SELECT TOP 30
                    wait_type,
                    waiting_tasks_count,
                    wait_time_ms,
                    signal_wait_time_ms,
                    wait_time_ms - signal_wait_time_ms AS resource_wait_time_ms,
                    CAST(100.0 * wait_time_ms / NULLIF(SUM(wait_time_ms) OVER (),0) AS DECIMAL(5,2)) AS percentual
                FROM sys.dm_os_wait_stats
                WHERE wait_type NOT IN (
                    'SLEEP_TASK','BROKER_TASK_STOP','BROKER_TO_FLUSH','SQLTRACE_BUFFER_FLUSH',
                    'CLR_AUTO_EVENT','CLR_MANUAL_EVENT','LAZYWRITER_SLEEP','RESOURCE_QUEUE',
                    'XE_DISPATCHER_WAIT','XE_TIMER_EVENT','FT_IFTS_SCHEDULER_IDLE_WAIT',
                    'BROKER_EVENTHANDLER','WAITFOR','CHECKPOINT_QUEUE','REQUEST_FOR_DEADLOCK_SEARCH',
                    'LOGMGR_QUEUE','ONDEMAND_TASK_QUEUE','DIRTY_PAGE_POLL','SP_SERVER_DIAGNOSTICS_SLEEP'
                )
                ORDER BY wait_time_ms DESC;
                """
        },
        new()
        {
            Id = 4, Name = "Latência de arquivos", Category = "I/O",
            Description = "Latência atual (janela curta de amostragem) de leitura e escrita por arquivo MDF/NDF/LDF; o acumulado desde o início da instância é só referência.",
            SqlFactory = t => IoLatencySql(onlyBenner: false, t)
        },
        new()
        {
            Id = 5, Name = "I/O do banco principal", Category = "I/O",
            Description = "Latência atual em uma janela curta de amostragem do banco principal configurado.",
            SqlFactory = t => IoLatencySql(onlyBenner: true, t)
        },
        new()
        {
            Id = 6, Name = "Top CPU", Category = "Queries",
            Description = "Queries com maior consumo acumulado de CPU.",
            StaticSql = """
                SELECT TOP 20
                    qs.execution_count,
                    qs.total_worker_time / 1000 AS total_cpu_ms,
                    (qs.total_worker_time / NULLIF(qs.execution_count,0)) / 1000 AS media_cpu_ms,
                    qs.total_elapsed_time / 1000 AS total_elapsed_ms,
                    (qs.total_elapsed_time / NULLIF(qs.execution_count,0)) / 1000 AS media_elapsed_ms,
                    qs.total_logical_reads,
                    qs.total_logical_writes,
                    DB_NAME(st.dbid) AS banco,
                    SUBSTRING(
                        st.text,
                        (qs.statement_start_offset / 2) + 1,
                        ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text)
                            ELSE qs.statement_end_offset END - qs.statement_start_offset) / 2) + 1
                    ) AS query
                FROM sys.dm_exec_query_stats qs
                CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
                ORDER BY qs.total_worker_time DESC;
                """
        },
        new()
        {
            Id = 7, Name = "Top leituras", Category = "Queries",
            Description = "Queries com maior volume de leituras lógicas.",
            StaticSql = """
                SELECT TOP 20
                    qs.execution_count,
                    qs.total_logical_reads,
                    qs.total_logical_reads / NULLIF(qs.execution_count,0) AS media_logical_reads,
                    qs.total_physical_reads,
                    qs.total_logical_writes,
                    qs.total_elapsed_time / 1000 AS total_elapsed_ms,
                    DB_NAME(st.dbid) AS banco,
                    st.text
                FROM sys.dm_exec_query_stats qs
                CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
                ORDER BY qs.total_logical_reads DESC;
                """
        },
        new()
        {
            Id = 8, Name = "Queries mais lentas", Category = "Queries",
            Description = "Maior tempo médio de execução.",
            StaticSql = """
                SELECT TOP 20
                    qs.execution_count,
                    CAST((qs.total_elapsed_time / NULLIF(qs.execution_count,0)) / 1000.0 AS DECIMAL(18,2)) AS tempo_medio_ms,
                    CAST(qs.total_elapsed_time / 1000.0 AS DECIMAL(18,2)) AS tempo_total_ms,
                    qs.total_logical_reads / NULLIF(qs.execution_count,0) AS leituras_media,
                    DB_NAME(st.dbid) AS banco,
                    st.text
                FROM sys.dm_exec_query_stats qs
                CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
                WHERE qs.execution_count > 0
                ORDER BY tempo_medio_ms DESC;
                """
        },
        new()
        {
            Id = 9, Name = "Memória SQL", Category = "Memória",
            Description = "Memória consumida pelo processo SQL Server.",
            StaticSql = """
                SELECT
                    physical_memory_in_use_kb / 1024 AS sql_memory_mb,
                    large_page_allocations_kb / 1024 AS large_pages_mb,
                    locked_page_allocations_kb / 1024 AS locked_pages_mb,
                    virtual_address_space_committed_kb / 1024 AS virtual_committed_mb,
                    process_physical_memory_low,
                    process_virtual_memory_low
                FROM sys.dm_os_process_memory;
                """
        },
        new()
        {
            Id = 10, Name = "Memória servidor / PLE", Category = "Memória",
            Description = "Memória do host e Page Life Expectancy.",
            StaticSql = """
                SELECT
                    total_physical_memory_kb / 1024 AS total_memory_mb,
                    available_physical_memory_kb / 1024 AS available_memory_mb,
                    total_page_file_kb / 1024 AS total_pagefile_mb,
                    available_page_file_kb / 1024 AS available_pagefile_mb,
                    system_memory_state_desc
                FROM sys.dm_os_sys_memory;

                SELECT
                    object_name,
                    counter_name,
                    cntr_value
                FROM sys.dm_os_performance_counters
                WHERE counter_name IN ('Page life expectancy','Buffer cache hit ratio','Buffer cache hit ratio base');
                """
        },
        new()
        {
            Id = 11, Name = "Arquivos e autogrowth", Category = "Storage",
            Description = "Tamanho, caminho e política de crescimento dos arquivos.",
            StaticSql = """
                SELECT
                    DB_NAME(database_id) AS banco,
                    name,
                    type_desc,
                    physical_name,
                    CAST(size * 8.0 / 1024 AS DECIMAL(18,2)) AS tamanho_mb,
                    CASE
                        WHEN is_percent_growth = 1 THEN CAST(growth AS VARCHAR(20)) + '%'
                        ELSE CAST(growth * 8.0 / 1024 AS VARCHAR(20)) + ' MB'
                    END AS crescimento
                FROM sys.master_files
                ORDER BY banco, type_desc;
                """
        },
        new()
        {
            Id = 12, Name = "Uso do LOG", Category = "Log",
            Description = "Percentual ocupado dos logs de transação.",
            StaticSql = "DBCC SQLPERF(LOGSPACE);"
        },
        new()
        {
            Id = 13, Name = "Sessões por banco", Category = "Sessões",
            Description = "Quantidade de requests ativos agrupados por banco.",
            StaticSql = """
                SELECT
                    DB_NAME(r.database_id) AS banco,
                    COUNT(*) AS sessoes
                FROM sys.dm_exec_sessions s
                INNER JOIN sys.dm_exec_requests r
                    ON s.session_id = r.session_id
                WHERE s.is_user_process = 1
                GROUP BY r.database_id
                ORDER BY sessoes DESC;
                """
        },
        new()
        {
            Id = 14, Name = "Snapshot instantâneo", Category = "Resumo",
            Description = "Requests ativos e esperas atuais para captura no momento da lentidão.",
            StaticSql = """
                SELECT
                    r.session_id,
                    r.blocking_session_id,
                    r.status,
                    r.command,
                    DB_NAME(r.database_id) AS banco,
                    r.cpu_time AS cpu_ms,
                    r.total_elapsed_time AS elapsed_ms,
                    r.logical_reads,
                    r.reads,
                    r.writes,
                    r.wait_type,
                    r.wait_time,
                    r.wait_resource,
                    s.login_name,
                    s.host_name,
                    s.program_name,
                    t.text AS sql_text
                FROM sys.dm_exec_requests r
                JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
                CROSS APPLY sys.dm_exec_sql_text(r.sql_handle) t
                WHERE r.session_id <> @@SPID
                ORDER BY r.total_elapsed_time DESC;
                """
        },
        new()
        {
            Id = 15, Name = "Backup FULL / LOGS", Category = "Backup",
            Description = "FULL de todos os bancos e integridade da janela/cadeia dos backups de LOG (msdb).",
            SqlFactory = BackupSql
        },
        new()
        {
            Id = 16, Name = "Espaço em disco", Category = "Storage",
            Description = "Espaço livre dos volumes que hospedam arquivos de banco (aviso 15%, crítico 10%).",
            StaticSql = DiskSql
        },
        new()
        {
            Id = 17, Name = "tempdb / version store", Category = "Storage",
            Description = "Ocupação do tempdb e version store retido por transações longas.",
            StaticSql = TempdbSql
        },
        new()
        {
            Id = 18, Name = "Transações longas", Category = "Sessões",
            Description = "Transações abertas há mais de 15 min (retêm log, version store e travas).",
            SqlFactory = LongTranSql
        },
        new()
        {
            Id = 19, Name = "Bloqueador-raiz", Category = "Locks",
            Description = "Quem segura o lock no topo da cadeia de bloqueio: sessão, host, transação aberta, locks mantidos e SQL (aviso ≥ 5 s, crítico ≥ 30 s de espera).",
            StaticSql = BlockingSql
        },
    };
}
