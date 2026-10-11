ALTER TABLE cm_teams ADD COLUMN IF NOT EXISTS whatsapp_group_ids text[] NOT NULL DEFAULT '{}';
UPDATE cm_teams
SET whatsapp_group_ids = ARRAY[whatsapp_group_id]
WHERE whatsapp_group_id IS NOT NULL
  AND whatsapp_group_id <> ''
  AND cardinality(whatsapp_group_ids) = 0;
