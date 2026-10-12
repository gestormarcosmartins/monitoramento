CREATE TABLE IF NOT EXISTS cm_push_subscriptions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES cm_users(id) ON DELETE CASCADE,
    endpoint text NOT NULL UNIQUE,
    p256dh text NOT NULL,
    auth text NOT NULL,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_success_at timestamptz,
    last_error text
);
CREATE INDEX IF NOT EXISTS ix_cm_push_subscriptions_user ON cm_push_subscriptions(user_id);

-- Deduplicação: um push por (incidente, evento, inscrição), mesmo se o outbox for reprocessado.
CREATE TABLE IF NOT EXISTS cm_push_deliveries (
    incident_id uuid NOT NULL,
    event text NOT NULL,
    subscription_id uuid NOT NULL REFERENCES cm_push_subscriptions(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (incident_id,event,subscription_id)
);
