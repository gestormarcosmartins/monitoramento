ALTER TABLE cm_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_assets FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_monitors ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_monitors FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_events FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_alerts FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_incidents FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_action_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_action_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE cm_action_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE cm_action_executions FORCE ROW LEVEL SECURITY;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'cm_assets','cm_monitors','cm_events','cm_alerts',
        'cm_incidents','cm_action_grants','cm_action_executions'
    ] LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid)',
            table_name
        );
    END LOOP;
END $$;

