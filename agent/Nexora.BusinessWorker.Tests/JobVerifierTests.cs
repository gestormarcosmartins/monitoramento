using Nexora.BusinessWorker;
using Xunit;

namespace Nexora.BusinessWorker.Tests;

public class JobVerifierTests
{
    [Fact]
    public void Accepts_a_job_signed_by_the_central_including_non_ascii_text()
    {
        var job = JobVerifier.Verify(Vectors.Get("valid"), Vectors.Options(), Vectors.Now);
        Assert.Equal("erp_principal", job.DatabaseRef);
        Assert.Equal(3, job.ColumnSchema.Count);
        Assert.Contains("N'ação'", job.Query);          // UTF-8 survives the round trip
        Assert.Equal(500, job.Constraints.MaxRows);
        Assert.Equal(10, job.Parameters["limite"].GetInt32());
    }

    [Fact]
    public void Rejects_a_tampered_payload()
    {
        var valid = Vectors.Get("valid");
        var tampered = new JobEnvelope { Payload = valid.Payload.Replace("SELECT TOP", "SELECT  TOP"), Signature = valid.Signature };
        Assert.Equal("invalid_signature", Assert.Throws<JobRejectedException>(() => JobVerifier.Verify(tampered, Vectors.Options(), Vectors.Now)).Code);
    }

    [Fact]
    public void Rejects_a_job_signed_by_another_key()
    {
        var options = Vectors.Options(o => o.SigningPublicKey = Convert.ToBase64String(new byte[32]));
        Assert.Equal("invalid_signature", Assert.Throws<JobRejectedException>(() => JobVerifier.Verify(Vectors.Get("valid"), options, Vectors.Now)).Code);
    }

    [Theory]
    [InlineData("not base64!!")]
    [InlineData("")]
    [InlineData("AAAA")]
    public void Garbage_signatures_are_rejected_not_thrown(string signature)
    {
        var envelope = new JobEnvelope { Payload = Vectors.Get("valid").Payload, Signature = signature };
        Assert.Equal("invalid_signature", Assert.Throws<JobRejectedException>(() => JobVerifier.Verify(envelope, Vectors.Options(), Vectors.Now)).Code);
    }

    [Theory]
    [InlineData("wrong_tenant", "tenant_mismatch")]
    [InlineData("wrong_worker", "worker_mismatch")]
    [InlineData("expired", "job_expired")]
    [InlineData("lease_expired", "lease_expired")]
    [InlineData("limits_too_big", "limits_out_of_range")]
    [InlineData("bad_dialect", "unsupported_dialect")]
    public void Validly_signed_but_unsafe_jobs_are_still_refused(string vector, string code)
    {
        Assert.Equal(code, Assert.Throws<JobRejectedException>(() => JobVerifier.Verify(Vectors.Get(vector), Vectors.Options(), Vectors.Now)).Code);
    }

    [Fact]
    public void Execution_id_is_only_exposed_for_jobs_addressed_to_this_worker()
    {
        Assert.NotNull(JobVerifier.TryReadExecutionId(Vectors.Get("valid").Payload, Vectors.Options()));
        Assert.Null(JobVerifier.TryReadExecutionId(Vectors.Get("wrong_tenant").Payload, Vectors.Options()));
        Assert.Null(JobVerifier.TryReadExecutionId("{not json", Vectors.Options()));
    }

    [Fact]
    public void Options_validation_demands_https_a_pinned_key_and_sane_intervals()
    {
        Assert.Empty(Vectors.Options().Validate());
        Assert.NotEmpty(Vectors.Options(o => o.ServerUrl = "http://nexora.example.test").Validate());
        Assert.Empty(Vectors.Options(o => o.ServerUrl = "http://localhost:8090").Validate());   // loopback development
        Assert.NotEmpty(Vectors.Options(o => o.SigningPublicKey = "").Validate());
        Assert.NotEmpty(Vectors.Options(o => o.PollSeconds = 1).Validate());
        Assert.NotEmpty(Vectors.Options(o => o.TenantId = "x").Validate());
    }
}
