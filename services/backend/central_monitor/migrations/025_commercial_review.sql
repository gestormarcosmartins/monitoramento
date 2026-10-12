INSERT INTO cm_permission_catalog(code,description,object_scope,sensitive) VALUES
('consumption.review','Revisar consumo comercial','company',true)
ON CONFLICT(code) DO NOTHING;
INSERT INTO cm_role_permissions(role,permission_code) VALUES
('admin','consumption.review'),('manager','consumption.review') ON CONFLICT DO NOTHING;

DO $$ DECLARE constraint_name text; BEGIN
  FOR constraint_name IN
    SELECT conname FROM pg_constraint
    WHERE conrelid='cm_consumption_ledger'::regclass AND contype='c'
      AND pg_get_constraintdef(oid) LIKE '%original_entry_id%'
  LOOP EXECUTE format('ALTER TABLE cm_consumption_ledger DROP CONSTRAINT %I',constraint_name); END LOOP;
END $$;
ALTER TABLE cm_consumption_ledger ADD CONSTRAINT cm_consumption_ledger_reference_check CHECK (
  (entry_type='effort' AND original_entry_id IS NULL) OR
  (entry_type IN ('commercial_adjustment','reversal') AND original_entry_id IS NOT NULL)
);
ALTER TABLE cm_consumption_ledger ADD CONSTRAINT cm_consumption_ledger_tenant_id_id_key UNIQUE(tenant_id,id);

CREATE TABLE IF NOT EXISTS cm_consumption_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL,
    ledger_entry_id uuid NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','superseded')),
    technical_minutes integer NOT NULL,
    proposed_minutes integer NOT NULL CHECK(proposed_minutes>=0),
    reason text NOT NULL,
    requested_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_by uuid REFERENCES cm_users(id) ON DELETE SET NULL,
    decided_at timestamptz,
    decision_note text,
    adjustment_entry_id uuid,
    snapshot jsonb NOT NULL,
    snapshot_hash text NOT NULL,
    FOREIGN KEY(tenant_id,ledger_entry_id) REFERENCES cm_consumption_ledger(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(adjustment_entry_id) REFERENCES cm_consumption_ledger(id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,ledger_entry_id,revision), UNIQUE(tenant_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_consumption_review_pending
ON cm_consumption_reviews(tenant_id,ledger_entry_id) WHERE state='pending';
CREATE INDEX IF NOT EXISTS ix_cm_consumption_review_queue
ON cm_consumption_reviews(tenant_id,state,requested_at);

ALTER TABLE cm_consumption_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_consumption_reviews FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_consumption_reviews;
CREATE POLICY tenant_isolation ON cm_consumption_reviews
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

CREATE OR REPLACE VIEW cm_consumption_commercial_state AS
SELECT l.tenant_id,l.id AS ledger_entry_id,l.raw_minutes AS technical_minutes,l.billable_minutes,
       COALESCE(r.state,CASE WHEN l.entry_type IN ('commercial_adjustment','reversal') THEN 'approved' ELSE 'unreviewed' END) AS commercial_state,
       r.proposed_minutes,r.id AS review_id,r.snapshot_hash
FROM cm_consumption_ledger l
LEFT JOIN LATERAL (
  SELECT cr.id,cr.state,cr.proposed_minutes,cr.snapshot_hash
  FROM cm_consumption_reviews cr
  WHERE cr.tenant_id=l.tenant_id AND cr.ledger_entry_id=l.id
  ORDER BY cr.revision DESC LIMIT 1
) r ON true;
