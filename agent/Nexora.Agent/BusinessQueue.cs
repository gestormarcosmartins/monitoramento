using System.Text.Json;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Options;

namespace Nexora.Agent;

/// <summary>Durable SQLite queue; ACK deletes only the event that the cloud has confirmed.</summary>
public sealed class BusinessQueue
{
    private readonly string _filename;
    private readonly SemaphoreSlim _gate = new(1,1);
    private const long MaxBytes = 256L * 1024 * 1024;
    private const int MaxPending = 50000;

    public BusinessQueue(IOptions<AgentOptions> options)
    {
        var directory = AgentPaths.DataDirectory(options.Value);
        Directory.CreateDirectory(directory);
        _filename = Path.Combine(directory, "business-events.sqlite3");
        using var c = Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS pending (
              event_id TEXT NOT NULL PRIMARY KEY, created_at TEXT NOT NULL,
              payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              last_error_code TEXT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_business_pending_created ON pending(created_at,event_id);
            CREATE TABLE IF NOT EXISTS rejected (
              event_id TEXT NOT NULL PRIMARY KEY, created_at TEXT NOT NULL, rejected_at TEXT NOT NULL,
              payload TEXT NOT NULL, error_code TEXT NOT NULL
            );
            """;
        cmd.ExecuteNonQuery();
    }

    private SqliteConnection Open()
    {
        var c = new SqliteConnection(new SqliteConnectionStringBuilder
        { DataSource = _filename, Mode = SqliteOpenMode.ReadWriteCreate, Cache = SqliteCacheMode.Private }.ToString());
        c.Open();
        using var busy = c.CreateCommand();
        busy.CommandText = "PRAGMA busy_timeout=5000";
        busy.ExecuteNonQuery();
        return c;
    }

    public async Task StoreAsync(BusinessSample sample, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try
        {
            using var c = Open();
            using var count = c.CreateCommand();
            count.CommandText = "SELECT COUNT(*) FROM pending";
            if ((long)(count.ExecuteScalar() ?? 0L) >= MaxPending || new FileInfo(_filename).Length >= MaxBytes)
                throw new IOException("Business queue capacity exceeded; operator action required");
            using var cmd = c.CreateCommand();
            cmd.CommandText = "INSERT OR IGNORE INTO pending(event_id,created_at,payload) VALUES($id,$at,$payload)";
            cmd.Parameters.AddWithValue("$id", sample.EventId);
            cmd.Parameters.AddWithValue("$at", DateTimeOffset.UtcNow.ToString("O"));
            cmd.Parameters.AddWithValue("$payload", JsonSerializer.Serialize(sample));
            cmd.ExecuteNonQuery();
        }
        finally { _gate.Release(); }
    }

    public async Task<List<BusinessSample>> PendingAsync(int limit, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try
        {
            using var c = Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = "SELECT event_id,payload FROM pending ORDER BY created_at,event_id LIMIT $limit";
            cmd.Parameters.AddWithValue("$limit", Math.Clamp(limit,1,200));
            var result = new List<BusinessSample>();
            var corrupt = new List<(string Id, string Payload)>();
            using (var reader = cmd.ExecuteReader())
            {
                while(reader.Read())
                {
                    BusinessSample? item = null;
                    try { item = JsonSerializer.Deserialize<BusinessSample>(reader.GetString(1)); } catch (JsonException) { }
                    if (item is null) corrupt.Add((reader.GetString(0), reader.GetString(1)));
                    else result.Add(item);
                }
            }
            // A corrupt payload must not break every flush cycle: quarantine it.
            foreach (var bad in corrupt) MoveToRejected(c, bad.Id, "InvalidPayload");
            return result;
        }
        finally { _gate.Release(); }
    }

    public async Task CompleteAsync(string eventId, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try
        {
            using var c = Open();
            using var cmd = c.CreateCommand();
            cmd.CommandText = "DELETE FROM pending WHERE event_id=$id";
            cmd.Parameters.AddWithValue("$id",eventId);
            cmd.ExecuteNonQuery();
        }
        finally { _gate.Release(); }
    }

    /// <summary>Permanently rejected by the cloud: keep for operator review, stop blocking newer events.</summary>
    public async Task QuarantineAsync(string eventId, string errorCode, CancellationToken token)
    {
        await _gate.WaitAsync(token);
        try
        {
            using var c = Open();
            MoveToRejected(c, eventId, errorCode);
        }
        finally { _gate.Release(); }
    }

    private static void MoveToRejected(SqliteConnection c, string eventId, string errorCode)
    {
        using var tx = c.BeginTransaction();
        using var cmd = c.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            INSERT OR REPLACE INTO rejected(event_id,created_at,rejected_at,payload,error_code)
              SELECT event_id,created_at,$now,payload,$code FROM pending WHERE event_id=$id;
            DELETE FROM pending WHERE event_id=$id;
            """;
        cmd.Parameters.AddWithValue("$id", eventId);
        cmd.Parameters.AddWithValue("$now", DateTimeOffset.UtcNow.ToString("O"));
        cmd.Parameters.AddWithValue("$code", errorCode);
        cmd.ExecuteNonQuery();
        tx.Commit();
    }

    public int Depth()
    {
        using var c = Open();
        using var cmd = c.CreateCommand();
        cmd.CommandText = "SELECT COUNT(*) FROM pending";
        return Convert.ToInt32(cmd.ExecuteScalar());
    }
}
