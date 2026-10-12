INSERT INTO cm_permission_catalog(code,description,object_scope,sensitive) VALUES
('profitability.read','Consultar rentabilidade','company',true),('billing.export','Exportar pré-faturamento','company',true)
ON CONFLICT(code) DO NOTHING;
INSERT INTO cm_role_permissions(role,permission_code) VALUES
('admin','profitability.read'),('admin','billing.export') ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS cm_contracts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES cm_tenants(id) ON DELETE CASCADE,
    engagement_id uuid NOT NULL, code text NOT NULL, name text NOT NULL,
    status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','suspended','ended')),
    starts_on date NOT NULL, ends_on date, active_version integer,
    created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,engagement_id) REFERENCES cm_service_engagements(tenant_id,id) ON DELETE RESTRICT,
    CHECK(ends_on IS NULL OR ends_on>=starts_on), UNIQUE(tenant_id,code), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_contract_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, contract_id uuid NOT NULL,
    version integer NOT NULL, currency char(3) NOT NULL DEFAULT 'BRL', included_minutes integer NOT NULL DEFAULT 0 CHECK(included_minutes>=0),
    rounding_minutes integer NOT NULL DEFAULT 1 CHECK(rounding_minutes>0), included_rate numeric(14,4), excess_rate numeric(14,4),
    internal_cost_rate numeric(14,4), coverage jsonb NOT NULL DEFAULT '{}'::jsonb, targets jsonb NOT NULL DEFAULT '{}'::jsonb,
    state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','review','published','retired')),
    effective_from date NOT NULL, effective_until date, content_hash text NOT NULL,
    created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
    FOREIGN KEY(tenant_id,contract_id) REFERENCES cm_contracts(tenant_id,id) ON DELETE CASCADE,
    CHECK(effective_until IS NULL OR effective_until>=effective_from), UNIQUE(tenant_id,contract_id,version), UNIQUE(tenant_id,id)
);
CREATE OR REPLACE FUNCTION cm_protect_published_contract_version() RETURNS trigger AS $$ BEGIN
  IF OLD.state='published' AND NEW IS DISTINCT FROM OLD THEN RAISE EXCEPTION 'published contract versions are immutable'; END IF; RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS protect_published_contract_version ON cm_contract_versions;
CREATE TRIGGER protect_published_contract_version BEFORE UPDATE OR DELETE ON cm_contract_versions FOR EACH ROW EXECUTE FUNCTION cm_protect_published_contract_version();

CREATE TABLE IF NOT EXISTS cm_consumption_ledger (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, contract_id uuid NOT NULL, contract_version_id uuid NOT NULL,
    ticket_id uuid, time_entry_id uuid, entry_type text NOT NULL CHECK(entry_type IN ('effort','commercial_adjustment','reversal')),
    source_key text NOT NULL, original_entry_id uuid, competence date NOT NULL, occurred_at timestamptz NOT NULL,
    raw_minutes integer NOT NULL, billable_minutes integer NOT NULL, included_minutes integer NOT NULL DEFAULT 0,
    excess_minutes integer NOT NULL DEFAULT 0, amount numeric(14,2), cost_amount numeric(14,2), description text NOT NULL,
    created_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,contract_id) REFERENCES cm_contracts(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,contract_version_id) REFERENCES cm_contract_versions(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,time_entry_id) REFERENCES cm_time_entries(tenant_id,id) ON DELETE RESTRICT,
    FOREIGN KEY(original_entry_id) REFERENCES cm_consumption_ledger(id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,source_key), CHECK(entry_type='reversal' OR original_entry_id IS NULL),
    CHECK(entry_type<>'reversal' OR original_entry_id IS NOT NULL)
);
CREATE OR REPLACE FUNCTION cm_immutable_ledger() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'consumption ledger is immutable; append an adjustment or reversal'; END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS immutable_consumption_ledger ON cm_consumption_ledger;
CREATE TRIGGER immutable_consumption_ledger BEFORE UPDATE OR DELETE ON cm_consumption_ledger FOR EACH ROW EXECUTE FUNCTION cm_immutable_ledger();

CREATE TABLE IF NOT EXISTS cm_billing_cycles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, contract_id uuid NOT NULL,
    competence date NOT NULL, external_key text NOT NULL, state text NOT NULL DEFAULT 'draft'
        CHECK(state IN ('draft','review','approved','exported','erp_confirmed','received','cancelled')),
    currency char(3) NOT NULL, total_minutes integer NOT NULL DEFAULT 0, included_minutes integer NOT NULL DEFAULT 0,
    excess_minutes integer NOT NULL DEFAULT 0, total_amount numeric(14,2), total_cost numeric(14,2),
    snapshot jsonb NOT NULL, snapshot_hash text NOT NULL, approved_snapshot_hash text,
    approved_by uuid REFERENCES cm_users(id) ON DELETE SET NULL, approved_at timestamptz,
    exported_at timestamptz, erp_reference text, erp_confirmed_at timestamptz, received_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,contract_id) REFERENCES cm_contracts(tenant_id,id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,contract_id,competence), UNIQUE(tenant_id,external_key), UNIQUE(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS cm_billing_lines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, cycle_id uuid NOT NULL,
    ledger_entry_id uuid NOT NULL, description text NOT NULL, billable_minutes integer NOT NULL,
    included_minutes integer NOT NULL, excess_minutes integer NOT NULL, amount numeric(14,2),
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,cycle_id) REFERENCES cm_billing_cycles(tenant_id,id) ON DELETE CASCADE,
    FOREIGN KEY(ledger_entry_id) REFERENCES cm_consumption_ledger(id) ON DELETE RESTRICT,
    UNIQUE(tenant_id,cycle_id,ledger_entry_id)
);

CREATE TABLE IF NOT EXISTS cm_csat_responses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, ticket_id uuid NOT NULL,
    respondent_id uuid REFERENCES cm_users(id) ON DELETE SET NULL, score integer NOT NULL CHECK(score BETWEEN 1 AND 5),
    comment text, created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(tenant_id,ticket_id) REFERENCES cm_incidents(tenant_id,id) ON DELETE CASCADE,
    UNIQUE(tenant_id,ticket_id,respondent_id)
);

CREATE INDEX IF NOT EXISTS ix_cm_ledger_competence ON cm_consumption_ledger(tenant_id,contract_id,competence,occurred_at);
CREATE INDEX IF NOT EXISTS ix_cm_billing_state ON cm_billing_cycles(tenant_id,state,competence DESC);

DO $$ DECLARE table_name text; BEGIN
  FOREACH table_name IN ARRAY ARRAY['cm_contracts','cm_contract_versions','cm_consumption_ledger','cm_billing_cycles','cm_billing_lines','cm_csat_responses'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name); EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I',table_name);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',table_name);
  END LOOP;
END $$;
