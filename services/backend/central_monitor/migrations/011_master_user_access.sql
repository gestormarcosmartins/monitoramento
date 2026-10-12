ALTER TABLE cm_users ADD COLUMN IF NOT EXISTS is_master boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cm_users_single_master
    ON cm_users (is_master) WHERE is_master;

UPDATE cm_users
SET is_master = true
WHERE id = (
    SELECT id FROM cm_users WHERE active ORDER BY created_at, id LIMIT 1
)
AND NOT EXISTS (SELECT 1 FROM cm_users WHERE is_master);
