-- Business Monitor P0/P1 expansion. Requires 045_business_monitor_mvp.sql.
-- RLS is mandatory and grants are explicit. Do not auto-enable product.
ALTER TABLE cm_business_grants DROP CONSTRAINT IF EXISTS cm_business_grants_scope_type_check;
ALTER TABLE cm_business_grants ADD COLUMN IF NOT EXISTS scope_id uuid;
ALTER TABLE cm_business_grants ADD CONSTRAINT cm_business_grants_scope_type_check
  CHECK(scope_type IN ('company','own','department','unit','process'));
ALTER TABLE cm_business_grants ADD CONSTRAINT cm_business_grants_scope_target_check
  CHECK ((scope_type IN ('company','own') AND scope_id IS NULL) OR
         (scope_type IN ('department','unit','process') AND scope_id IS NOT NULL));
ALTER TABLE cm_business_grants DROP CONSTRAINT IF EXISTS cm_business_grants_capability_check;
ALTER TABLE cm_business_grants ADD CONSTRAINT cm_business_grants_capability_check CHECK (capability IN (
  'business.view','business.monitor.create','business.monitor.update','business.monitor.inactivate',
  'business.occurrence.manage','business.source.manage','business.grants.manage',
  'business.rule.manage','business.process.manage','business.collector.test',
  'business.collector.publish','business.export','business.notifications.manage'));

CREATE TABLE cm_business_units (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES cm_tenants(id),
 name text NOT NULL CHECK(length(trim(name)) BETWEEN 2 AND 150), active bool NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,name));
CREATE TABLE cm_business_processes (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES cm_tenants(id),
 unit_id uuid, department_id uuid, name text NOT NULL CHECK(length(trim(name)) BETWEEN 2 AND 180),
 description text NOT NULL DEFAULT '', owner_user_id uuid REFERENCES cm_users(id), active bool NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,name),
 FOREIGN KEY(tenant_id,unit_id) REFERENCES cm_business_units(tenant_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,department_id) REFERENCES cm_departments(tenant_id,id) ON DELETE RESTRICT);
CREATE TABLE cm_business_process_steps (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 process_id uuid NOT NULL, step_code varchar(64) NOT NULL, name text NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 1000),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','done','blocked','cancelled')),
 due_at timestamptz, finished_at timestamptz, owner_user_id uuid REFERENCES cm_users(id),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,process_id,step_code), UNIQUE(tenant_id,process_id,ordinal),
 FOREIGN KEY(tenant_id,process_id) REFERENCES cm_business_processes(tenant_id,id) ON DELETE RESTRICT);
CREATE TABLE cm_business_step_dependencies (
 tenant_id uuid NOT NULL, predecessor_id uuid NOT NULL, successor_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,predecessor_id,successor_id), CHECK(predecessor_id<>successor_id),
 FOREIGN KEY(tenant_id,predecessor_id) REFERENCES cm_business_process_steps(tenant_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(tenant_id,successor_id) REFERENCES cm_business_process_steps(tenant_id,id) ON DELETE RESTRICT);
ALTER TABLE cm_business_indicators ADD COLUMN IF NOT EXISTS unit_id uuid;
ALTER TABLE cm_business_indicators ADD COLUMN IF NOT EXISTS department_id uuid;
ALTER TABLE cm_business_indicators ADD COLUMN IF NOT EXISTS process_id uuid;
ALTER TABLE cm_business_indicators ADD CONSTRAINT cm_bi_unit_fk FOREIGN KEY(tenant_id,unit_id) REFERENCES cm_business_units(tenant_id,id);
ALTER TABLE cm_business_indicators ADD CONSTRAINT cm_bi_dept_fk FOREIGN KEY(tenant_id,department_id) REFERENCES cm_departments(tenant_id,id);
ALTER TABLE cm_business_indicators ADD CONSTRAINT cm_bi_process_fk FOREIGN KEY(tenant_id,process_id) REFERENCES cm_business_processes(tenant_id,id);

CREATE TABLE cm_business_collector_defs (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 agent_id uuid NOT NULL, indicator_id uuid NOT NULL, kind text NOT NULL CHECK(kind IN ('sql_view','postgres_view','rest_get','csv','xlsx')),
 name text NOT NULL, config jsonb NOT NULL DEFAULT '{}'::jsonb,
 state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','review','published','suspended')),
 current_revision integer NOT NULL DEFAULT 0, created_by uuid NOT NULL REFERENCES cm_users(id),
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id),
 FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id));
CREATE TABLE cm_business_collector_revisions (
 tenant_id uuid NOT NULL, collector_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
 config jsonb NOT NULL, sha256 char(64) NOT NULL, approved_by uuid NOT NULL REFERENCES cm_users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,collector_id,revision),
 FOREIGN KEY(tenant_id,collector_id) REFERENCES cm_business_collector_defs(tenant_id,id));
CREATE TABLE cm_business_collector_runs (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL, collector_id uuid NOT NULL,
 collected_at timestamptz, received_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL CHECK(status IN ('success','error','stale','rejected')),
 error_code text, duration_ms int, records_count int,
 FOREIGN KEY(tenant_id,collector_id) REFERENCES cm_business_collector_defs(tenant_id,id));

CREATE TABLE cm_business_rule_policies (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 indicator_id uuid NOT NULL, owner_user_id uuid NOT NULL REFERENCES cm_users(id),
 version integer NOT NULL DEFAULT 1,
 active bool NOT NULL DEFAULT true,
 warning_value numeric(22,6), critical_value numeric(22,6),
 direction text NOT NULL DEFAULT 'above' CHECK(direction IN ('above','below')),
 hysteresis numeric(22,6) NOT NULL DEFAULT 0 CHECK(hysteresis>=0),
 cooldown_seconds integer NOT NULL DEFAULT 0 CHECK(cooldown_seconds BETWEEN 0 AND 604800),
 timezone text NOT NULL DEFAULT 'America/Sao_Paulo',
 weekdays integer[] NOT NULL DEFAULT ARRAY[1,2,3,4,5,6,7],
 check_from time, check_until time,
 stale_after_seconds integer NOT NULL DEFAULT 1800 CHECK(stale_after_seconds BETWEEN 30 AND 2592000),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,indicator_id),
 FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id),
 CHECK((warning_value IS NULL AND critical_value IS NULL) OR
       (warning_value IS NOT NULL AND critical_value IS NOT NULL AND
       ((direction='above' AND warning_value < critical_value) OR
        (direction='below' AND warning_value > critical_value)))));
CREATE TABLE cm_business_rule_evaluations (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 rule_id uuid NOT NULL, rule_version int NOT NULL,
 indicator_id uuid NOT NULL, state text NOT NULL CHECK(state IN ('normal','atencao','critico','sem_dados','suspenso')),
 reason text NOT NULL, evaluated_at timestamptz NOT NULL DEFAULT now(),
 value numeric(22,6),
 FOREIGN KEY(tenant_id,rule_id) REFERENCES cm_business_rule_policies(tenant_id,id),
 FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id));
CREATE TABLE cm_business_occurrence_history (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 occurrence_id uuid NOT NULL, actor_user_id uuid REFERENCES cm_users(id),
 action text NOT NULL, details jsonb NOT NULL DEFAULT '{}'::jsonb,
 occurred_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,occurrence_id) REFERENCES cm_business_occurrences(tenant_id,id));
CREATE TABLE cm_business_notifications (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 recipient_user_id uuid NOT NULL REFERENCES cm_users(id),
 occurrence_id uuid,
 title text NOT NULL, message text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), read_at timestamptz,
 FOREIGN KEY(tenant_id,occurrence_id) REFERENCES cm_business_occurrences(tenant_id,id));
CREATE TABLE cm_business_notification_subscriptions (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES cm_users(id),
 indicator_id uuid NOT NULL, enabled bool NOT NULL DEFAULT true,
 PRIMARY KEY(tenant_id,user_id,indicator_id),
 FOREIGN KEY(tenant_id,indicator_id) REFERENCES cm_business_indicators(tenant_id,id));
CREATE TABLE cm_business_ingestion_errors (
 id uuid DEFAULT gen_random_uuid() PRIMARY KEY, tenant_id uuid NOT NULL,
 agent_id uuid NOT NULL, indicator_id uuid, event_id text,
 error_code text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,agent_id) REFERENCES cm_agents(tenant_id,id));

CREATE INDEX ix_business_processes_owner ON cm_business_processes(tenant_id,owner_user_id,department_id,unit_id);
CREATE INDEX ix_business_process_steps_due ON cm_business_process_steps(tenant_id,process_id,due_at);
CREATE INDEX ix_business_rule_evaluations_time ON cm_business_rule_evaluations(tenant_id,rule_id,evaluated_at DESC);
CREATE INDEX ix_business_notifications_user ON cm_business_notifications(tenant_id,recipient_user_id,created_at DESC);
CREATE INDEX ix_business_collectors_agent ON cm_business_collector_defs(tenant_id,agent_id,state);
CREATE INDEX ix_business_grants_target ON cm_business_grants(tenant_id,user_id,capability,scope_type,scope_id) WHERE active;

DO $$ DECLARE tab text; BEGIN
 FOREACH tab IN ARRAY ARRAY[
  'cm_business_units','cm_business_processes','cm_business_process_steps','cm_business_step_dependencies',
  'cm_business_collector_defs','cm_business_collector_revisions','cm_business_collector_runs',
  'cm_business_rule_policies','cm_business_rule_evaluations','cm_business_occurrence_history',
  'cm_business_notifications','cm_business_notification_subscriptions','cm_business_ingestion_errors'
 ] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tab);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tab);
  EXECUTE format('CREATE POLICY business_tenant ON %I USING (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id=nullif(current_setting(''app.tenant_id'',true),'''')::uuid)', tab);
 END LOOP;
END $$;
-- No grants or notifications are created by migration. Feature remains disabled by default.
