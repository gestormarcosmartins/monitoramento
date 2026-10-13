-- cm_alerts had UNIQUE(tenant_id,fingerprint,status). A monitor that flaps (goes up/down
-- repeatedly, e.g. memory usage oscillating around its 90% threshold) accumulates one
-- 'resolved' row per recovery for the same fingerprint, since open->resolved transitions
-- reuse the SAME row, but a later down->open->resolved cycle creates a NEW row (a fresh
-- INSERT since no 'open' row exists at that moment) which then collides with the OLDER
-- 'resolved' row for that fingerprint when it is itself later resolved — a plain UPDATE,
-- not an upsert, so there is no ON CONFLICT to absorb it. Observed on Morada/SRVHYPERAMC:
-- the exact same "duplicate key value violates unique constraint
-- cm_alerts_tenant_id_fingerprint_status_key" aborted record_result() partway through
-- processing a snapshot's results, silently discarding every result after the failing one
-- (CPU's monitor row kept updating; memory/disk/services, later in the same list, did not).
--
-- The real invariant is "only one OPEN alert per fingerprint at a time" — resolved/ignored
-- history for the same fingerprint should be allowed to repeat. Replace the full unique
-- constraint with a partial unique index that only applies to open alerts.
ALTER TABLE cm_alerts DROP CONSTRAINT IF EXISTS cm_alerts_tenant_id_fingerprint_status_key;

CREATE UNIQUE INDEX IF NOT EXISTS cm_alerts_open_fingerprint_key
    ON cm_alerts (tenant_id, fingerprint)
    WHERE status = 'open';
