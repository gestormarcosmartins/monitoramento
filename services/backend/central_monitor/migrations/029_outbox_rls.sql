ALTER TABLE cm_outbox ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE cm_outbox ADD CONSTRAINT cm_outbox_tenant_id_id_key UNIQUE(tenant_id,id);
ALTER TABLE cm_outbox_attempts ADD COLUMN IF NOT EXISTS tenant_id uuid;
UPDATE cm_outbox_attempts a SET tenant_id=o.tenant_id FROM cm_outbox o WHERE o.id=a.outbox_id AND a.tenant_id IS NULL;
ALTER TABLE cm_outbox_attempts ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE cm_outbox_attempts ADD CONSTRAINT cm_outbox_attempts_tenant_outbox_fkey
FOREIGN KEY(tenant_id,outbox_id) REFERENCES cm_outbox(tenant_id,id) ON DELETE CASCADE;
ALTER TABLE cm_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_outbox FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_outbox;
CREATE POLICY tenant_isolation ON cm_outbox
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);

ALTER TABLE cm_outbox_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_outbox_attempts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON cm_outbox_attempts;
CREATE POLICY tenant_isolation ON cm_outbox_attempts
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
