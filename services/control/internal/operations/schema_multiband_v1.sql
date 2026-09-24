-- Run after operations schema v3, with management intake drained.
-- New data remain independent candidates. No legacy pointers are rewritten.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('rainpulse-operations-schema-v1',0));
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM ops_schema WHERE version=3) THEN
  RAISE EXCEPTION 'operations schema v3 required';
 END IF;
END $$;
ALTER TABLE ops_pool_controls DROP CONSTRAINT IF EXISTS ops_pool_controls_kind_check;
ALTER TABLE ops_pool_controls ADD CONSTRAINT ops_pool_controls_kind_check CHECK(kind IN ('qc','render','diagnostics','multiband'));
ALTER TABLE ops_release_channels DROP CONSTRAINT IF EXISTS ops_release_channels_kind_check;
ALTER TABLE ops_release_channels ADD CONSTRAINT ops_release_channels_kind_check CHECK(kind IN ('qc','render','diagnostics','multiband'));
INSERT INTO ops_pool_controls(kind,mode,reason) VALUES('multiband','DRAINING','Initial X/S integration requires explicit acceptance and resume') ON CONFLICT DO NOTHING;
INSERT INTO ops_release_channels(kind) VALUES('multiband') ON CONFLICT DO NOTHING;
INSERT INTO ops_schema(version) VALUES(4) ON CONFLICT DO NOTHING;
COMMIT;
