ALTER TABLE cm_monitors DROP CONSTRAINT IF EXISTS cm_monitors_monitor_type_check;
ALTER TABLE cm_monitors ADD CONSTRAINT cm_monitors_monitor_type_check
    CHECK (monitor_type IN ('http','tcp','zabbix','event_deadman','push'));

CREATE TABLE IF NOT EXISTS cm_benner_snapshots (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    asset_id uuid NOT NULL, execution_id text NOT NULL, collected_at timestamptz NOT NULL,
    finished_at timestamptz, duration_ms numeric, target jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb, backup jsonb, metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_payload jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES cm_assets(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (tenant_id, execution_id), UNIQUE (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS cm_benner_checks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    snapshot_id uuid NOT NULL, monitor_id uuid NOT NULL, check_number integer NOT NULL,
    name text NOT NULL, category text, status text NOT NULL, severity text NOT NULL,
    duration_ms numeric, message text, result_sets jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, snapshot_id) REFERENCES cm_benner_snapshots(tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, monitor_id) REFERENCES cm_monitors(tenant_id, id) ON DELETE CASCADE,
    UNIQUE (snapshot_id, check_number)
);

CREATE INDEX IF NOT EXISTS ix_benner_snapshots_tenant_date ON cm_benner_snapshots(tenant_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS ix_benner_checks_snapshot ON cm_benner_checks(snapshot_id, check_number);
ALTER TABLE cm_benner_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_benner_checks ENABLE ROW LEVEL SECURITY;
CREATE POLICY benner_snapshots_tenant_isolation ON cm_benner_snapshots
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
CREATE POLICY benner_checks_tenant_isolation ON cm_benner_checks
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
