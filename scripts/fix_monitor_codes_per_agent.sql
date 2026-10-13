\set ON_ERROR_STOP on

-- GET /agent/config returns every 'push' monitor for the whole tenant, so a bare code
-- like "host-cpu" or "sql-connectivity" collides once more than one Windows agent shares
-- it in the same tenant (confirmed crashing SRVAQAHYPERV02's agent with "An item with
-- the same key has already been added"). The agent and backend now provision codes as
-- "<code>:<agent name>"; this backfills the monitors created before that fix.
BEGIN;

UPDATE cm_monitors
SET configuration = jsonb_set(configuration, '{code}', to_jsonb(configuration->>'code' || ':Morada - SRVMORADABD'))
WHERE tenant_id = '398c3a5e-bc75-48e9-b982-53eccae7eafb'
  AND name LIKE 'SRVMORADABD - %'
  AND configuration ? 'code'
  AND configuration->>'code' NOT LIKE '%:%';

UPDATE cm_monitors
SET configuration = jsonb_set(configuration, '{code}', to_jsonb(configuration->>'code' || ':Morada - SRVHYPERAMC'))
WHERE tenant_id = '398c3a5e-bc75-48e9-b982-53eccae7eafb'
  AND name LIKE 'Morada - SRVHYPERAMC - %'
  AND configuration ? 'code'
  AND configuration->>'code' NOT LIKE '%:%';

UPDATE cm_monitors
SET configuration = jsonb_set(configuration, '{code}', to_jsonb(configuration->>'code' || ':Morada - SRVAQAHYPERV02'))
WHERE tenant_id = '398c3a5e-bc75-48e9-b982-53eccae7eafb'
  AND name LIKE 'Morada - SRVAQAHYPERV02 - %'
  AND configuration ? 'code'
  AND configuration->>'code' NOT LIKE '%:%';

COMMIT;
