using System.Text.Json;

namespace Nexora.Agent;

public sealed class LocalQueue
{
    private readonly string _directory;

    public LocalQueue(Microsoft.Extensions.Options.IOptions<AgentOptions> options)
    {
        _directory = Path.Combine(AgentPaths.DataDirectory(options.Value), "queue");
        Directory.CreateDirectory(_directory);
    }

    public async Task EnqueueAsync(SnapshotEnvelope snapshot, CancellationToken cancellationToken)
    {
        var finalName = Path.Combine(_directory, $"{DateTimeOffset.UtcNow:yyyyMMddHHmmssfff}-{Guid.NewGuid():N}.json");
        var temporary = finalName + ".tmp";
        await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(snapshot), cancellationToken);
        File.Move(temporary, finalName);
    }

    public IEnumerable<string> Files() => Directory.EnumerateFiles(_directory, "*.json").OrderBy(x => x);
    public async Task<SnapshotEnvelope?> ReadAsync(string path, CancellationToken cancellationToken) =>
        JsonSerializer.Deserialize<SnapshotEnvelope>(await File.ReadAllTextAsync(path, cancellationToken));
    public void Complete(string path) => File.Delete(path);
}
