using System.Text;
using System.Text.Json;
using Xunit;
using Microsoft.Extensions.Logging.Abstractions;
using Nexora.BusinessWorker;

namespace Nexora.BusinessWorker.Tests;

/// <summary>Signed remote update, worker side. Manifests come from the central's real signing code (tools/gen_worker_vectors.py).</summary>
public class UpdateStagerTests : IDisposable
{
    private static readonly string Platform = UpdateStager.CurrentPlatform();
    private static readonly DateTimeOffset OfferTime = DateTimeOffset.Parse("2026-10-14T10:00:00+00:00");
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "nx-upd-" + Guid.NewGuid().ToString("N")[..8]);

    public void Dispose() { try { Directory.Delete(_dir, true); } catch (IOException) { } }

    private static string V(string kind) => $"{kind}_{Platform}";
    private WorkerOptions Options() => Vectors.Options(o => o.UpdateDirectory = _dir);

    private static UpdateManifest Verify(string vector, string current = "1.0.0", bool staged = false, DateTimeOffset? now = null)
        => UpdateStager.VerifyManifest(Vectors.Update(vector), Vectors.Options(), current, Platform, now ?? OfferTime, staged);

    private static string CodeOf(Action action) => Assert.Throws<UpdateRejectedException>(action).Code;

    [Fact]
    public void Platform_is_known_on_the_machines_that_run_the_tests()
    {
        Assert.Contains(Platform, new[] { "win-x64", "linux-x64" });
    }

    [Fact]
    public void A_valid_offer_is_accepted_and_carries_the_signed_fields()
    {
        var manifest = Verify(V("ok"));
        Assert.Equal("1.1.0", manifest.Version);
        Assert.False(manifest.Rollback);
        Assert.Equal(Platform, manifest.Platform);
        Assert.Equal(Vectors.Package.Length, manifest.SizeBytes);
    }

    [Theory]
    [InlineData("wrong_tenant", "tenant_mismatch")]
    [InlineData("wrong_worker", "worker_mismatch")]
    [InlineData("expired_offer", "manifest_expired")]
    [InlineData("downgrade", "not_an_update")]
    [InlineData("same", "not_an_update")]
    public void Offers_that_do_not_fit_this_worker_are_refused(string kind, string expected)
    {
        Assert.Equal(expected, CodeOf(() => Verify(V(kind))));
    }

    [Fact]
    public void A_manifest_for_another_platform_is_refused()
    {
        Assert.Equal("platform_mismatch", CodeOf(() => Verify("wrong_platform_" + Platform)));
    }

    [Fact]
    public void A_rollback_manifest_may_go_to_an_older_version_but_a_plain_one_may_not()
    {
        Assert.Equal("0.9.0", Verify(V("rollback")).Version);
        Assert.True(Verify(V("rollback")).Rollback);
        Assert.Equal("not_an_update", CodeOf(() => Verify(V("downgrade"))));
    }

    [Fact]
    public void A_tampered_payload_or_signature_fails_before_anything_else_is_read()
    {
        var good = Vectors.Update(V("ok"));
        var flipped = new JobEnvelope { Payload = good.Payload.Replace("1.1.0", "9.9.9"), Signature = good.Signature };
        Assert.Equal("invalid_signature", CodeOf(() => UpdateStager.VerifyManifest(flipped, Vectors.Options(), "1.0.0", Platform, OfferTime, false)));
        var other = new JobEnvelope { Payload = good.Payload, Signature = Vectors.Get("valid").Signature };
        Assert.Equal("invalid_signature", CodeOf(() => UpdateStager.VerifyManifest(other, Vectors.Options(), "1.0.0", Platform, OfferTime, false)));
        var wrongKey = Vectors.Options(o => o.SigningPublicKey = Convert.ToBase64String(new byte[32]));
        Assert.Equal("invalid_signature", CodeOf(() => UpdateStager.VerifyManifest(good, wrongKey, "1.0.0", Platform, OfferTime, false)));
    }

    [Fact]
    public void An_unknown_current_version_never_updates()
    {
        Assert.Equal("unknown_current_version", CodeOf(() => Verify(V("ok"), current: "dev")));
    }

    [Fact]
    public void A_staged_package_stays_valid_for_a_day_but_a_replayed_old_manifest_does_not()
    {
        Assert.Equal("1.1.0", Verify(V("ok"), staged: true, now: OfferTime.AddHours(20)).Version);
        Assert.Equal("manifest_expired", CodeOf(() => Verify(V("old"), staged: true)));
        Assert.Equal("manifest_expired", CodeOf(() => Verify(V("ok"), staged: true, now: OfferTime.AddHours(30))));
    }

    // ---------------------------------------------------------------- staging
    private sealed class FakeApi(JobEnvelope? offer, byte[] payload) : ICentralApi
    {
        public List<(string Version, string Status, string Error)> Reports = new();
        public int Downloads;
        public Task HeartbeatAsync(CancellationToken ct) => Task.CompletedTask;
        public Task<IReadOnlyList<JobEnvelope>> ClaimAsync(int limit, CancellationToken ct) => Task.FromResult<IReadOnlyList<JobEnvelope>>(new List<JobEnvelope>());
        public Task<bool> StartedAsync(Guid id, CancellationToken ct) => Task.FromResult(true);
        public Task<bool> RenewAsync(Guid id, CancellationToken ct) => Task.FromResult(true);
        public Task<string> ResultAsync(Guid id, ResultBody body, CancellationToken ct) => Task.FromResult("accepted");
        public Task FailedAsync(Guid id, FailureBody body, CancellationToken ct) => Task.CompletedTask;
        public Task<JobEnvelope?> CheckUpdateAsync(string platform, CancellationToken ct) => Task.FromResult(offer);
        public async Task DownloadReleaseAsync(string version, string platform, Stream destination, long maxBytes, CancellationToken ct)
        {
            Downloads++;
            if (payload.Length > maxBytes) throw new UpdateRejectedException("size_mismatch");
            await destination.WriteAsync(payload, ct);
        }
        public Task ReportUpdateAsync(string version, string status, string error, CancellationToken ct) { Reports.Add((version, status, error)); return Task.CompletedTask; }
    }

    private sealed class FixedTime(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    private (UpdateStager Stager, FakeApi Api) Build(JobEnvelope? offer, string? package = null)
    {
        var api = new FakeApi(offer, Encoding.UTF8.GetBytes(package ?? Vectors.Package));
        return (new UpdateStager(api, Options(), NullLogger<UpdateStager>.Instance, new FixedTime(OfferTime)), api);
    }

    private string Ready(string file) => Path.Combine(_dir, "ready", file);

    [Fact]
    public async Task A_verified_package_is_staged_with_its_manifest_and_reported()
    {
        var (stager, api) = Build(Vectors.Update(V("ok")));
        Assert.True(await stager.TryStageAsync(CancellationToken.None));
        Assert.Equal(Vectors.Package, await File.ReadAllTextAsync(Ready("package.pkg")));
        var pending = JsonSerializer.Deserialize<PendingUpdate>(await File.ReadAllTextAsync(Ready("pending.json")))!;
        Assert.Equal(("1.1.0", Platform), (pending.Version, pending.Platform));
        Assert.Equal(("1.1.0", "DOWNLOADED", ""), api.Reports.Single());
        Assert.Empty(Directory.GetFiles(Path.Combine(_dir, "incoming")));  // no partial file left behind
        Assert.Equal("ok", UpdateStager.VerifyStaged(Options(), Path.Combine(_dir, "ready"), OfferTime, "1.0.0"));
    }

    [Fact]
    public async Task The_same_package_is_not_downloaded_twice()
    {
        var (stager, api) = Build(Vectors.Update(V("ok")));
        Assert.True(await stager.TryStageAsync(CancellationToken.None));
        Assert.False(await stager.TryStageAsync(CancellationToken.None));
        Assert.Equal(1, api.Downloads);
    }

    [Fact]
    public async Task No_offer_means_nothing_happens()
    {
        var (stager, api) = Build(null);
        Assert.False(await stager.TryStageAsync(CancellationToken.None));
        Assert.Equal(0, api.Downloads);
        Assert.False(Directory.Exists(Ready("")) && File.Exists(Ready("pending.json")));
    }

    [Fact]
    public async Task A_package_whose_hash_differs_is_discarded_and_reported_as_failed()
    {
        var (stager, api) = Build(Vectors.Update(V("ok")), package: "package-1.1.X");  // same length, different content
        Assert.False(await stager.TryStageAsync(CancellationToken.None));
        Assert.False(File.Exists(Ready("package.pkg")));
        Assert.False(File.Exists(Ready("pending.json")));
        Assert.Empty(Directory.GetFiles(Path.Combine(_dir, "incoming")));
        Assert.Equal(("1.1.0", "FAILED", "hash_mismatch"), api.Reports.Single());
    }

    [Fact]
    public async Task A_package_larger_than_promised_is_refused_while_downloading()
    {
        var (stager, api) = Build(Vectors.Update(V("ok")), package: Vectors.Package + "-and-much-more");
        Assert.False(await stager.TryStageAsync(CancellationToken.None));
        Assert.Equal("size_mismatch", api.Reports.Single().Error);
        Assert.False(File.Exists(Ready("package.pkg")));
    }

    [Fact]
    public async Task A_rejected_offer_is_reported_without_downloading()
    {
        var (stager, api) = Build(Vectors.Update(V("wrong_tenant")));
        Assert.False(await stager.TryStageAsync(CancellationToken.None));
        Assert.Equal(0, api.Downloads);
        Assert.Equal("tenant_mismatch", api.Reports.Single().Error);
    }

    [Fact]
    public async Task A_new_package_replaces_the_manifest_it_no_longer_matches()
    {
        var (stager, _) = Build(Vectors.Update(V("ok")));
        await stager.TryStageAsync(CancellationToken.None);
        File.WriteAllText(Ready("pending.json"), "{}");  // stale/foreign manifest next to the package
        Assert.NotEqual("ok", UpdateStager.VerifyStaged(Options(), Path.Combine(_dir, "ready"), OfferTime, "1.0.0"));
    }

    // ---------------------------------------------------------------- re-verification before applying (apply script)
    [Fact]
    public async Task Verify_staged_catches_a_swapped_package_a_missing_file_and_an_old_manifest()
    {
        var (stager, _) = Build(Vectors.Update(V("ok")));
        await stager.TryStageAsync(CancellationToken.None);
        var ready = Path.Combine(_dir, "ready");
        Assert.Equal("ok", UpdateStager.VerifyStaged(Options(), ready, OfferTime, "1.0.0"));
        Assert.Equal("manifest_expired", UpdateStager.VerifyStaged(Options(), ready, OfferTime.AddHours(30), "1.0.0"));
        Assert.Equal("not_an_update", UpdateStager.VerifyStaged(Options(), ready, OfferTime, "1.1.0"));  // already running it

        File.WriteAllText(Ready("package.pkg"), "package-1.1.X");  // swapped after staging
        Assert.Equal("hash_mismatch", UpdateStager.VerifyStaged(Options(), ready, OfferTime, "1.0.0"));
        File.WriteAllText(Ready("package.pkg"), "short");
        Assert.Equal("size_mismatch", UpdateStager.VerifyStaged(Options(), ready, OfferTime, "1.0.0"));
        File.Delete(Ready("package.pkg"));
        Assert.Equal("nothing_staged", UpdateStager.VerifyStaged(Options(), ready, OfferTime, "1.0.0"));
    }

    [Fact]
    public void Verify_staged_refuses_garbage_without_throwing()
    {
        Directory.CreateDirectory(Path.Combine(_dir, "ready"));
        File.WriteAllText(Ready("pending.json"), "not json");
        File.WriteAllText(Ready("package.pkg"), "x");
        Assert.Equal("unreadable", UpdateStager.VerifyStaged(Options(), Path.Combine(_dir, "ready"), OfferTime, "1.0.0"));
    }

    [Theory]
    [InlineData(0, true)]
    [InlineData(5, true)]
    [InlineData(1440, true)]
    [InlineData(4, false)]
    [InlineData(1441, false)]
    public void Update_interval_is_validated(int minutes, bool valid)
    {
        var problems = Vectors.Options(o => o.UpdateCheckMinutes = minutes).Validate();
        Assert.Equal(valid, problems.Count == 0);
    }
}
