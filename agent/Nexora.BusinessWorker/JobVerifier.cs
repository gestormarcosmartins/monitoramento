using System.Text;
using System.Text.Json;
using Org.BouncyCastle.Crypto.Parameters;
using Org.BouncyCastle.Crypto.Signers;

namespace Nexora.BusinessWorker;

/// <summary>
/// Verifies a job BEFORE anything in it is trusted: the Ed25519 signature is checked over the exact payload bytes
/// the central produced, then the job is parsed from those same bytes and bound to this tenant, worker and time.
/// </summary>
public static class JobVerifier
{
    public static bool VerifySignature(string payload, string signatureBase64, string publicKeyBase64)
    {
        try
        {
            var key = new Ed25519PublicKeyParameters(Convert.FromBase64String(publicKeyBase64), 0);
            var signature = Convert.FromBase64String(signatureBase64);
            var bytes = Encoding.UTF8.GetBytes(payload);
            var verifier = new Ed25519Signer();
            verifier.Init(false, key);
            verifier.BlockUpdate(bytes, 0, bytes.Length);
            return verifier.VerifySignature(signature);
        }
        catch (Exception ex) when (ex is FormatException or ArgumentException or Org.BouncyCastle.Crypto.CryptoException)
        {
            return false;
        }
    }

    /// <summary>Returns the verified job or throws <see cref="JobRejectedException"/> with a stable code.</summary>
    public static Job Verify(JobEnvelope envelope, WorkerOptions options, DateTimeOffset now)
    {
        if (!VerifySignature(envelope.Payload, envelope.Signature, options.SigningPublicKey))
            throw new JobRejectedException("invalid_signature");
        Job? job;
        try { job = JsonSerializer.Deserialize<Job>(envelope.Payload); }
        catch (JsonException) { throw new JobRejectedException("invalid_payload"); }
        if (job is null || job.ExecutionId == Guid.Empty) throw new JobRejectedException("invalid_payload");
        if (!Guid.TryParse(options.TenantId, out var tenant) || job.TenantId != tenant) throw new JobRejectedException("tenant_mismatch");
        if (!Guid.TryParse(options.WorkerId, out var worker) || job.WorkerId != worker) throw new JobRejectedException("worker_mismatch");
        if (job.ExpiresAt <= now) throw new JobRejectedException("job_expired");
        if (job.LeaseExpiresAt <= now) throw new JobRejectedException("lease_expired");
        if (job.Dialect is not ("sqlserver" or "postgres")) throw new JobRejectedException("unsupported_dialect");
        if (job.ColumnSchema.Count == 0 || string.IsNullOrWhiteSpace(job.Query)) throw new JobRejectedException("invalid_payload");
        var limits = job.Constraints;
        if (limits.TimeoutSeconds is < 1 or > 60 || limits.MaxRows is < 1 or > 5000 || limits.MaxBytes is < 1 or > 5 * 1024 * 1024)
            throw new JobRejectedException("limits_out_of_range");  // never trust a manifest that exceeds the platform ceilings
        return job;
    }

    /// <summary>Best effort id of a REJECTED job so the central can close it; only when it is addressed to this worker.</summary>
    public static Guid? TryReadExecutionId(string payload, WorkerOptions options)
    {
        try
        {
            using var document = JsonDocument.Parse(payload);
            var root = document.RootElement;
            if (root.TryGetProperty("tenant_id", out var t) && t.GetString() == options.TenantId
                && root.TryGetProperty("worker_id", out var w) && w.GetString() == options.WorkerId
                && root.TryGetProperty("execution_id", out var e) && Guid.TryParse(e.GetString(), out var id))
                return id;
        }
        catch (JsonException) { }
        return null;
    }
}
