-- New verification jobs require 30 six-minute truth frames (three hours).
-- Existing research records remain readable; no historical artifacts are deleted.
DO $$
DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'forecast_verification_runs'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%cardinality(truth_%'
    LOOP
        EXECUTE format('ALTER TABLE forecast_verification_runs DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;

ALTER TABLE forecast_verification_runs
    ADD CONSTRAINT truth_frame_count_six_minute CHECK (
        cardinality(truth_analysis_ids) = 30 AND
        cardinality(truth_valid_times) = 30 AND
        cardinality(truth_uris) = 30 AND
        cardinality(truth_sha256s) = 30
    ) NOT VALID;
