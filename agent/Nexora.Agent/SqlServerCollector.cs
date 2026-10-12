using System.Text.Json;
using Microsoft.Data.SqlClient;
using Microsoft.Extensions.Options;

namespace Nexora.Agent;

public sealed class SqlServerCollector
{
    private readonly AgentOptions _options;
    private readonly SqlCredentialStore _password;
    public SqlServerCollector(IOptions<AgentOptions> options, SqlCredentialStore password) { _options=options.Value; _password=password; }

    private SqlConnection Connection()
    {
        var builder = new SqlConnectionStringBuilder {
            DataSource=_options.SqlServer, InitialCatalog=_options.SqlDatabase, IntegratedSecurity=_options.SqlIntegratedSecurity,
            Encrypt=_options.SqlEncrypt, TrustServerCertificate=_options.SqlTrustServerCertificate,
            ConnectTimeout=Math.Clamp(_options.SqlCommandTimeoutSeconds,5,120), ApplicationName="Nexora Agent"
        };
        if (!_options.SqlIntegratedSecurity) { builder.UserID=_options.SqlUser; builder.Password=_password.Load(); }
        return new SqlConnection(builder.ConnectionString);
    }

    private async Task<List<Dictionary<string,object?>>> QueryAsync(string sql, CancellationToken token)
    {
        await using var connection=Connection(); await connection.OpenAsync(token);
        await using var command=new SqlCommand(sql,connection){CommandTimeout=Math.Clamp(_options.SqlCommandTimeoutSeconds,5,300)};
        await using var reader=await command.ExecuteReaderAsync(token); var rows=new List<Dictionary<string,object?>>();
        do { while(await reader.ReadAsync(token)) { var row=new Dictionary<string,object?>(); for(var i=0;i<reader.FieldCount;i++) row[reader.GetName(i)]=reader.IsDBNull(i)?null:reader.GetValue(i); rows.Add(row); } } while(await reader.NextResultAsync(token));
        return rows;
    }

    public async Task<Dictionary<string,object?>> HealthAsync(CancellationToken token)
    {
        if(!_options.SqlEnabled) return new(){["enabled"]=false};
        var started=DateTimeOffset.UtcNow;
        try {
            var rows=await QueryAsync("SELECT @@SERVERNAME server_name, DB_NAME() database_name, CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) version; SELECT name,state_desc,user_access_desc FROM sys.databases ORDER BY name",token);
            return new(){["enabled"]=true,["ok"]=true,["server"]=_options.SqlServer,["database"]=_options.SqlDatabase,["collected_at"]=started,["details"]=rows};
        } catch(Exception error) { return new(){["enabled"]=true,["ok"]=false,["server"]=_options.SqlServer,["database"]=_options.SqlDatabase,["collected_at"]=started,["error"]=error.Message}; }
    }

    public async Task<SnapshotEnvelope?> OperationalAsync(AgentConfiguration config, CancellationToken token)
    {
        if(!_options.SqlEnabled) return null;
        var monitors=config.Monitors.Select(m=>new {m,code=Code(m.Configuration)}).Where(x=>x.code is not null).ToDictionary(x=>x.code!,x=>x.m,StringComparer.OrdinalIgnoreCase);
        var results=new List<AgentResult>(); var now=DateTimeOffset.UtcNow;
        async Task Add(string code,string sql,Func<List<Dictionary<string,object?>>, (bool ok,string message)> evaluate) {
            if(!monitors.TryGetValue(code,out var monitor)) return;
            try { var evaluation=evaluate(await QueryAsync(sql,token)); results.Add(new(monitor.Id,evaluation.ok,evaluation.ok?"up":"down",evaluation.message,now)); }
            catch(Exception e){ results.Add(new(monitor.Id,false,"unknown",e.Message,now)); }
        }
        await Add("sql-connectivity","SELECT @@SERVERNAME server_name",r=>(true,$"SQL Server online: {r.FirstOrDefault()?.GetValueOrDefault("server_name")}"));
        await Add("database-status","SELECT name,state_desc FROM sys.databases WHERE state_desc<>'ONLINE'",r=>(r.Count==0,r.Count==0?"Todos os bancos estão online":$"{r.Count} banco(s) fora de ONLINE"));
        await Add("blocking","SELECT session_id,blocking_session_id,wait_time FROM sys.dm_exec_requests WHERE blocking_session_id<>0",r=>(r.Count==0,r.Count==0?"Sem bloqueios":"Há sessões bloqueadas: "+r.Count));
        await Add("long-transactions","SELECT s.session_id FROM sys.dm_tran_active_transactions t JOIN sys.dm_tran_session_transactions st ON st.transaction_id=t.transaction_id JOIN sys.dm_exec_sessions s ON s.session_id=st.session_id WHERE DATEDIFF(MINUTE,t.transaction_begin_time,GETDATE())>=15",r=>(r.Count==0,r.Count==0?"Sem transações acima de 15 minutos":$"{r.Count} transação(ões) longa(s)"));
        return results.Count==0?null:new($"sql-health-{now:yyyyMMddHHmmss}-{Guid.NewGuid():N}",now,results,new(){["source"]="sql-server-direct",["profile"]="operational_health",["server"]=_options.SqlServer,["database"]=_options.SqlDatabase});
    }

    public async Task<SnapshotEnvelope?> PerformanceAsync(AgentConfiguration config,CancellationToken token)
    {
        if(!_options.SqlEnabled) return null;
        var monitor=config.Monitors.FirstOrDefault(m=>string.Equals(Code(m.Configuration),"sql-performance",StringComparison.OrdinalIgnoreCase)); if(monitor is null)return null;
        var now=DateTimeOffset.UtcNow;
        try { var rows=await QueryAsync("SELECT TOP 10 qs.execution_count,qs.total_worker_time/1000 total_cpu_ms,qs.total_logical_reads,qs.total_elapsed_time/1000 total_elapsed_ms FROM sys.dm_exec_query_stats qs ORDER BY qs.total_worker_time DESC",token); return new($"sql-performance-{now:yyyyMMddHHmmss}-{Guid.NewGuid():N}",now,[new(monitor.Id,true,"up",$"Análise concluída: {rows.Count} principais consultas avaliadas.",now)],new(){["source"]="sql-server-direct",["profile"]="performance",["recommendations"]="Revisar consultas de maior CPU e leituras; nenhuma alteração foi executada.",["sample_size"]=rows.Count}); }
        catch(Exception error){return new($"sql-performance-{now:yyyyMMddHHmmss}-{Guid.NewGuid():N}",now,[new(monitor.Id,false,"unknown",error.Message,now)],new(){["source"]="sql-server-direct",["profile"]="performance"});}
    }
    private static string? Code(JsonElement e)=>e.ValueKind==JsonValueKind.Object&&(e.TryGetProperty("code",out var c)||e.TryGetProperty("check_id",out c))?c.ToString():null;
}
