using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Nexora.Agent;
using Nexora.BusinessWorker;
using Org.BouncyCastle.Crypto.Parameters;
using Org.BouncyCastle.Crypto.Signers;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Atualização automática do agente: a verificação precisa recusar tudo que não seja exatamente a oferta assinada para ESTE agente.</summary>
public class AgentUpdateTests
{
    private static readonly Ed25519PrivateKeyParameters PrivateKey = new(Enumerable.Range(0, 32).Select(i => (byte)i).ToArray(), 0);
    private static readonly string PublicKey = Convert.ToBase64String(PrivateKey.GeneratePublicKey().GetEncoded());
    private static readonly Guid Tenant = Guid.Parse("b83a2863-6380-4401-a604-eb1cd3338cfe");
    private static readonly Guid Agent = Guid.Parse("4b1eb96b-b9c9-42ab-a1e1-14167b4e9187");
    private static readonly DateTimeOffset Now = new(2026, 10, 17, 12, 0, 0, TimeSpan.Zero);
    private const string Hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    private static string Sign(string payload)
    {
        var signer = new Ed25519Signer();
        signer.Init(true, PrivateKey);
        var bytes = Encoding.UTF8.GetBytes(payload);
        signer.BlockUpdate(bytes, 0, bytes.Length);
        return Convert.ToBase64String(signer.GenerateSignature());
    }

    private static string Payload(string version = "2.2.1", string type = "agent_update", Guid? tenant = null, Guid? agent = null, string platform = "win-x64",
        string sha = Hash, long size = 1000, bool rollback = false, DateTimeOffset? issued = null, DateTimeOffset? expires = null) =>
        JsonSerializer.Serialize(new Dictionary<string, object>
        {
            ["type"] = type, ["tenant_id"] = (tenant ?? Tenant).ToString(), ["agent_id"] = (agent ?? Agent).ToString(), ["version"] = version, ["platform"] = platform,
            ["sha256"] = sha, ["size_bytes"] = size, ["rollback"] = rollback, ["issued_at"] = (issued ?? Now).ToString("o"), ["expires_at"] = (expires ?? Now.AddHours(1)).ToString("o"),
        });

    private static AgentUpdateManifest Verify(string payload, string? signature = null, string current = "2.2.0", string platform = "win-x64", DateTimeOffset? now = null, bool staged = false, string? key = null) =>
        AgentUpdateVerifier.VerifyManifest(payload, signature ?? Sign(payload), key ?? PublicKey, Tenant, Agent, current, platform, now ?? Now, staged);

    private static string CodeOf(Action action) => Assert.Throws<UpdateRejectedException>(action).Code;

    [Fact]
    public void A_correct_signed_offer_for_this_agent_is_accepted()
    {
        var manifest = Verify(Payload());
        Assert.Equal("2.2.1", manifest.Version);
        Assert.Equal(Hash, manifest.Sha256);
    }

    [Fact]
    public void A_tampered_payload_or_a_wrong_key_is_rejected()
    {
        var payload = Payload();
        Assert.Equal("invalid_signature", CodeOf(() => Verify(payload.Replace("2.2.1", "9.9.9"), Sign(payload))));
        var otherKey = Convert.ToBase64String(new Ed25519PrivateKeyParameters(Enumerable.Range(1, 32).Select(i => (byte)i).ToArray(), 0).GeneratePublicKey().GetEncoded());
        Assert.Equal("invalid_signature", CodeOf(() => Verify(payload, key: otherKey)));
        Assert.Equal("invalid_signature", CodeOf(() => Verify(payload, signature: "AAAA")));
    }

    [Fact]
    public void An_offer_made_for_another_tenant_agent_or_platform_is_rejected()
    {
        Assert.Equal("tenant_mismatch", CodeOf(() => Verify(Payload(tenant: Guid.NewGuid()))));
        Assert.Equal("agent_mismatch", CodeOf(() => Verify(Payload(agent: Guid.NewGuid()))));
        Assert.Equal("platform_mismatch", CodeOf(() => Verify(Payload(platform: "linux-x64"))));
        Assert.Equal("platform_mismatch", CodeOf(() => Verify(Payload(), platform: "")));
        Assert.Equal("invalid_manifest", CodeOf(() => Verify(Payload(type: "worker_update"))));  // um manifesto do Worker nunca vale para o agente
    }

    [Fact]
    public void Offers_expire_and_staged_packages_have_a_longer_but_finite_life()
    {
        Assert.Equal("manifest_expired", CodeOf(() => Verify(Payload(), now: Now.AddHours(2))));
        Verify(Payload(), now: Now.AddHours(10), staged: true);                                  // preparado há 10 h ainda vale
        Assert.Equal("manifest_expired", CodeOf(() => Verify(Payload(), now: Now.AddHours(25), staged: true)));
    }

    [Fact]
    public void Only_a_newer_version_is_an_update_and_rollback_must_differ_from_the_current()
    {
        Assert.Equal("not_an_update", CodeOf(() => Verify(Payload("2.2.0"))));
        Assert.Equal("not_an_update", CodeOf(() => Verify(Payload("2.1.9"))));                  // nunca rebaixa sozinho
        Verify(Payload("2.1.9", rollback: true));                                                // volta explícita (versão revogada) vale
        Assert.Equal("not_an_update", CodeOf(() => Verify(Payload("2.2.0", rollback: true))));
        Verify(Payload("2.10.0"), current: "2.9.0");                                             // comparação numérica, não alfabética
        Assert.Equal("unknown_current_version", CodeOf(() => Verify(Payload(), current: "garbage")));
    }

    [Fact]
    public void Malformed_fields_are_rejected_with_stable_codes()
    {
        Assert.Equal("invalid_version", CodeOf(() => Verify(Payload("2.2"))));
        Assert.Equal("invalid_hash", CodeOf(() => Verify(Payload(sha: "XYZ"))));
        Assert.Equal("invalid_size", CodeOf(() => Verify(Payload(size: 0))));
        Assert.Equal("invalid_size", CodeOf(() => Verify(Payload(size: UpdateStager.MaxPackageBytes + 1))));
        Assert.Equal("invalid_manifest", CodeOf(() => Verify("{ not json", Sign("{ not json"))));
    }

    private static (string Dir, byte[] Package) Staged(Func<string, string>? payloadFor = null, bool tamperPackage = false)
    {
        var dir = Path.Combine(Path.GetTempPath(), "nx-agent-staged-" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(dir);
        var package = Encoding.UTF8.GetBytes("pacote de teste do agente");
        var sha = Convert.ToHexString(SHA256.HashData(package)).ToLowerInvariant();
        var payload = payloadFor?.Invoke(sha) ?? Payload(sha: sha, size: package.Length);
        File.WriteAllBytes(Path.Combine(dir, "package.pkg"), tamperPackage ? package.Append((byte)1).ToArray() : package);
        File.WriteAllText(Path.Combine(dir, "pending.json"), JsonSerializer.Serialize(new PendingAgentUpdate { Version = "2.2.1", Platform = "win-x64", Sha256 = sha, Payload = payload, Signature = Sign(payload), StagedAt = Now }));
        return (dir, package);
    }

    private static string VerifyStaged(string dir, DateTimeOffset? now = null) =>
        AgentUpdateVerifier.VerifyStaged(dir, PublicKey, Tenant, Agent, "2.2.0", "win-x64", now ?? Now.AddMinutes(5));

    [Fact]
    public void A_staged_package_that_matches_its_signed_manifest_is_ok()
    {
        var (dir, _) = Staged();
        try { Assert.Equal("ok", VerifyStaged(dir)); } finally { Directory.Delete(dir, true); }
    }

    [Fact]
    public void A_staged_package_that_was_altered_after_staging_is_refused()
    {
        var (dir, _) = Staged(tamperPackage: true);
        try { Assert.Equal("size_mismatch", VerifyStaged(dir)); } finally { Directory.Delete(dir, true); }
        var (same, _) = Staged();
        try
        {
            var bytes = File.ReadAllBytes(Path.Combine(same, "package.pkg"));
            bytes[0] ^= 0xFF;                                                                    // mesmo tamanho, conteúdo diferente
            File.WriteAllBytes(Path.Combine(same, "package.pkg"), bytes);
            Assert.Equal("hash_mismatch", VerifyStaged(same));
        }
        finally { Directory.Delete(same, true); }
    }

    [Fact]
    public void Missing_corrupt_or_stale_staging_is_refused_without_throwing()
    {
        var empty = Path.Combine(Path.GetTempPath(), "nx-agent-empty-" + Guid.NewGuid().ToString("N")[..8]);
        Directory.CreateDirectory(empty);
        try
        {
            Assert.Equal("nothing_staged", VerifyStaged(empty));
            File.WriteAllText(Path.Combine(empty, "package.pkg"), "x");
            File.WriteAllText(Path.Combine(empty, "pending.json"), "{ nope");
            Assert.Equal("unreadable", VerifyStaged(empty));
        }
        finally { Directory.Delete(empty, true); }
        var (dir, _) = Staged();
        try { Assert.Equal("manifest_expired", VerifyStaged(dir, Now.AddDays(3))); } finally { Directory.Delete(dir, true); }
    }

    [Fact]
    public void The_agent_reports_a_real_parseable_version_and_updates_are_on_by_default_but_the_ring_decides()
    {
        Assert.True(UpdateStager.TryParseVersion(AgentVersion.Current, out _));
        var options = new AgentOptions();
        Assert.Equal(60, options.UpdateCheckMinutes);
        Assert.Equal("NexoraAgentUpdate", options.UpdateTaskName);
        Assert.False(string.IsNullOrWhiteSpace(options.UpdateSigningPublicKey));
        Assert.Equal(32, Convert.FromBase64String(options.UpdateSigningPublicKey).Length);   // chave Ed25519 válida
    }
}
