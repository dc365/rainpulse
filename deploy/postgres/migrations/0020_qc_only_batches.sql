ALTER TABLE pipeline_regeneration_requests ALTER COLUMN source_run_id DROP NOT NULL;
ALTER TABLE pipeline_regeneration_requests ALTER COLUMN target_run_id DROP NOT NULL;
ALTER TABLE pipeline_regeneration_requests DROP CONSTRAINT pipeline_regeneration_requests_preset_check;
ALTER TABLE pipeline_regeneration_requests ADD CHECK (preset IN ('forecast_all','radar_qc_only'));
ALTER TABLE pipeline_regeneration_requests ADD CHECK (preset = 'radar_qc_only' OR (source_run_id IS NOT NULL AND target_run_id IS NOT NULL));
CREATE TABLE qc_batch_items (
 request_id UUID NOT NULL REFERENCES pipeline_regeneration_requests(request_id),
 kind TEXT NOT NULL CHECK (kind IN ('qc','display')),
 item_id UUID NOT NULL,
 item_time TIMESTAMPTZ NOT NULL,
 radar_id TEXT NOT NULL DEFAULT '',
 job_id UUID REFERENCES jobs(job_id),
 status TEXT NOT NULL DEFAULT 'PENDING',
 error_message TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(request_id,kind,item_id)
);
CREATE INDEX qc_batch_items_target ON qc_batch_items(kind,item_id);
