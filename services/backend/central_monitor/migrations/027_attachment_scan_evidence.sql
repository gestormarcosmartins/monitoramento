ALTER TABLE cm_ticket_attachments ADD COLUMN IF NOT EXISTS scan_engine text;
ALTER TABLE cm_ticket_attachments ADD COLUMN IF NOT EXISTS scan_reference text;
ALTER TABLE cm_ticket_attachments ADD COLUMN IF NOT EXISTS scan_signature_version text;
ALTER TABLE cm_ticket_attachments ADD COLUMN IF NOT EXISTS rejected_reason text;
