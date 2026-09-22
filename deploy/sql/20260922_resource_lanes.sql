-- Explicit, additive preparation. Run BEFORE enabling resource routing.
-- Disabled routing does not query these columns. No data or queues are deleted.
-- ALTER TABLE needs a short lock: time out rather than blocking live operations.
SET lock_timeout = '5s';
SET statement_timeout = '30s';
ALTER TABLE outbox_events
    ADD COLUMN IF NOT EXISTS resource_route_frozen boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS resource_route_reason text;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='outbox_events'
          AND column_name='resource_route_frozen' AND data_type='boolean' AND is_nullable='NO'
          AND column_default='false')
       OR NOT EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name='outbox_events'
          AND column_name='resource_route_reason' AND data_type='text') THEN
        RAISE EXCEPTION 'Resource route column definition differs; inspect locally';
    END IF;
END $$;
