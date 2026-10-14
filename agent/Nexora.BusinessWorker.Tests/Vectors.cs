using System.Text.Json;
using Nexora.BusinessWorker;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Signed jobs produced by the central's real signing code (tools/gen_worker_vectors.py).</summary>
public static class Vectors
{
    private static readonly JsonElement Root = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "TestVectors.json"))).RootElement;

    public static string PublicKey => Root.GetProperty("public_key").GetString()!;
    public static DateTimeOffset Now => DateTimeOffset.Parse(Root.GetProperty("now").GetString()!);

    public static WorkerOptions Options(Action<WorkerOptions>? tweak = null)
    {
        var options = new WorkerOptions
        {
            ServerUrl = "https://nexora.example.test", TenantId = Root.GetProperty("tenant_id").GetString()!,
            WorkerId = Root.GetProperty("worker_id").GetString()!, SigningPublicKey = PublicKey,
        };
        tweak?.Invoke(options);
        return options;
    }

    public static string Package => Root.GetProperty("package").GetString()!;

    public static JobEnvelope Update(string name)
    {
        var item = Root.GetProperty("updates").GetProperty(name);
        return new JobEnvelope { Payload = item.GetProperty("payload").GetString()!, Signature = item.GetProperty("signature").GetString()! };
    }

    public static JobEnvelope Get(string name)
    {
        var item = Root.GetProperty("vectors").GetProperty(name);
        return new JobEnvelope { Payload = item.GetProperty("payload").GetString()!, Signature = item.GetProperty("signature").GetString()! };
    }
}
