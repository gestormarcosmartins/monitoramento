-- Broker dispatch bookkeeping for Business jobs: when the message was confirmed by RabbitMQ (null = never published).
ALTER TABLE cm_business_executions ADD COLUMN IF NOT EXISTS published_at timestamptz;
