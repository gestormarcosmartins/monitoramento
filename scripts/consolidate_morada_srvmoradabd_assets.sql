\set ON_ERROR_STOP on

-- Merges the two "same physical machine" environments created for
-- 172.19.19.219/SRVMORADABD into one: the pre-existing "Banco de Dados -
-- 172.19.19.219 - BENNER_LOG_OFICIAL" asset (19 log-based monitors) and the
-- "SRVMORADABD - SQL Server" asset created by the new Windows agent's
-- auto-provisioning (5 SQL monitors). All monitors move onto the older
-- asset, and the now-empty duplicate is removed.
BEGIN;

DO $$
DECLARE
    morada_id constant uuid := '398c3a5e-bc75-48e9-b982-53eccae7eafb';
    target_asset constant uuid := 'bc2f9da7-8caa-4e96-99cc-56f0ef3cfd34'; -- BENNER_LOG_OFICIAL
    source_asset constant uuid := 'af972e95-4224-45b5-a634-9dfca3923e93'; -- SRVMORADABD - SQL Server
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cm_tenants WHERE id = morada_id AND name = 'Morada' AND active) THEN
        RAISE EXCEPTION 'Active Morada tenant was not found';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cm_assets WHERE id = target_asset AND tenant_id = morada_id) THEN
        RAISE EXCEPTION 'Target asset % not found for Morada', target_asset;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cm_assets WHERE id = source_asset AND tenant_id = morada_id) THEN
        RAISE EXCEPTION 'Source asset % not found for Morada', source_asset;
    END IF;

    UPDATE cm_monitors SET asset_id = target_asset WHERE tenant_id = morada_id AND asset_id = source_asset;

    DELETE FROM cm_assets WHERE id = source_asset AND tenant_id = morada_id;
END $$;

COMMIT;
