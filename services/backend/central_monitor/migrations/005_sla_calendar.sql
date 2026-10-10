CREATE TABLE IF NOT EXISTS cm_holidays (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    holiday_date date NOT NULL,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id,holiday_date)
);
ALTER TABLE cm_holidays ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_holidays FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cm_holidays
USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
