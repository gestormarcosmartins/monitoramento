param(
    [string]$ComposeProjectDirectory = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$sql = @'
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE audit_tenants ON COMMIT DROP AS
SELECT id,row_number() OVER(ORDER BY id::text) AS position FROM cm_tenants WHERE active LIMIT 2;
GRANT SELECT ON audit_tenants TO central_app;
SET LOCAL ROLE central_app;
DO $audit$
DECLARE
  tenant_a uuid;
  tenant_b uuid;
  table_name text;
  leaked bigint;
  visible_a bigint;
  visible_b bigint;
BEGIN
  SELECT ids[1],ids[2] INTO tenant_a,tenant_b FROM (SELECT array_agg(id ORDER BY position) AS ids FROM audit_tenants) selected;
  IF tenant_a IS NULL OR tenant_b IS NULL OR tenant_a=tenant_b THEN
    RAISE EXCEPTION 'At least two active tenants are required for the adversarial RLS test';
  END IF;

  PERFORM set_config('app.tenant_id',tenant_a::text,true);
  FOREACH table_name IN ARRAY ARRAY[
    'cm_assets','cm_incidents','cm_incident_comments','cm_ticket_attachments',
    'cm_consumption_ledger','cm_billing_cycles','cm_outbox','cm_integration_inbox','cm_runbook_executions',
    'cm_reconciliation_cursors','cm_reconciliation_runs','cm_reconciliation_findings',
    'cm_departments','cm_user_departments'
  ] LOOP
    EXECUTE format('SELECT count(*) FROM %I WHERE tenant_id<>%L::uuid',table_name,tenant_a) INTO leaked;
    IF leaked<>0 THEN RAISE EXCEPTION 'RLS leak in % for tenant A: % row(s)',table_name,leaked; END IF;
  END LOOP;
  SELECT count(*) INTO visible_a FROM cm_assets;

  PERFORM set_config('app.tenant_id',tenant_b::text,true);
  FOREACH table_name IN ARRAY ARRAY[
    'cm_assets','cm_incidents','cm_incident_comments','cm_ticket_attachments',
    'cm_consumption_ledger','cm_billing_cycles','cm_outbox','cm_integration_inbox','cm_runbook_executions',
    'cm_reconciliation_cursors','cm_reconciliation_runs','cm_reconciliation_findings',
    'cm_departments','cm_user_departments'
  ] LOOP
    EXECUTE format('SELECT count(*) FROM %I WHERE tenant_id<>%L::uuid',table_name,tenant_b) INTO leaked;
    IF leaked<>0 THEN RAISE EXCEPTION 'RLS leak in % for tenant B after connection reuse: % row(s)',table_name,leaked; END IF;
  END LOOP;
  SELECT count(*) INTO visible_b FROM cm_assets;
  RAISE NOTICE 'RLS adversarial test passed; tenant_a=%, tenant_b=%, visible_assets_a=%, visible_assets_b=%',tenant_a,tenant_b,visible_a,visible_b;
END $audit$;
ROLLBACK;
'@

Push-Location -LiteralPath $ComposeProjectDirectory
try {
    $sql | docker compose exec -T postgres psql -U platform_admin -d central_monitor
    if ($LASTEXITCODE -ne 0) { throw "RLS adversarial verification failed with exit code $LASTEXITCODE" }
}
finally {
    Pop-Location
}
