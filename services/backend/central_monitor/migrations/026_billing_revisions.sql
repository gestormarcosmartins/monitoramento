ALTER TABLE cm_billing_cycles ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1 CHECK(revision>0);
ALTER TABLE cm_billing_cycles DROP CONSTRAINT IF EXISTS cm_billing_cycles_tenant_id_contract_id_competence_key;
ALTER TABLE cm_billing_cycles ADD CONSTRAINT cm_billing_cycles_tenant_contract_competence_revision_key
UNIQUE(tenant_id,contract_id,competence,revision);
CREATE INDEX IF NOT EXISTS ix_cm_billing_latest_revision
ON cm_billing_cycles(tenant_id,contract_id,competence,revision DESC);
