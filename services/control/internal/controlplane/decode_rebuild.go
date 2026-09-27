package controlplane

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
)

// Rebuild only normalized draft X input. Old immutable objects and job receipts
// remain available; the catalog returns missing while the new decoder runs.
func radarDecodeRebuild(ctx context.Context, store *postgresstore.Store, service *orchestration.Service, scanText, configPath, attemptText string) error {
	scanID, err := uuid.Parse(scanText)
	if err != nil {
		return err
	}
	attempt, err := uuid.Parse(attemptText)
	if err != nil || attempt == uuid.Nil {
		return fmt.Errorf("nonzero decode rebuild UUID required")
	}
	scan, err := store.GetRadarScan(ctx, scanID)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(configPath)
	if err != nil {
		return err
	}
	var cfg radarConfiguration
	if err = yaml.Unmarshal(raw, &cfg); err != nil {
		return err
	}
	if cfg.RadarID != scan.RadarID {
		return fmt.Errorf("decode rebuild radar differs")
	}
	var document map[string]any
	if err = yaml.Unmarshal(raw, &document); err != nil {
		return err
	}
	configJSON, err := json.Marshal(document)
	if err != nil {
		return err
	}
	jobs, err := store.ListJobs(ctx, scan.RunID)
	if err != nil {
		return err
	}
	var source *orchestration.RadarDecodeRequested
	for _, j := range jobs {
		if j.JobType != orchestration.RadarDecodeJobType {
			continue
		}
		var candidate orchestration.RadarDecodeRequested
		if err = json.Unmarshal(j.RequestPayload, &candidate); err != nil {
			return err
		}
		if candidate.Payload.ScanID != scanID {
			return fmt.Errorf("decode source scan differs")
		}
		if source != nil && (source.Payload.InputSHA256 != candidate.Payload.InputSHA256 || source.Payload.InputURI != candidate.Payload.InputURI) {
			return fmt.Errorf("ambiguous immutable decode source")
		}
		source = &candidate
	}
	if source == nil {
		return fmt.Errorf("no frozen decode request")
	}
	digest := sha256.Sum256(raw)
	_, job, err := service.CreateRadarDecode(ctx, orchestration.RadarDecodeInput{
		RadarID: scan.RadarID, DisplayName: &cfg.DisplayName, Lifecycle: workflow.RadarLifecycle(cfg.Lifecycle),
		ConfigVersion: cfg.ConfigVersion, Config: configJSON, ConfigSHA256: fmt.Sprintf("%x", digest), SourceFormat: cfg.Source.Format,
		InputURI: source.Payload.InputURI, InputSHA256: source.Payload.InputSHA256, InputSizeBytes: source.Payload.InputSizeBytes,
		VolumeStartTime: scan.VolumeStartTime, VolumeEndTime: scan.VolumeEndTime, ExistingRunID: scan.RunID, RebuildID: attempt,
	})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{"scan_id": scanID.String(), "job_id": job.ID.String(), "rebuild_id": attempt.String()})
}
