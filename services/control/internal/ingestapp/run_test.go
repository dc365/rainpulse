package ingestapp

import (
	"context"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/bdpruntime"
	"path/filepath"
	"testing"
)

func TestMissingManifestReturnsError(t *testing.T) {
	t.Setenv("RAINPULSE_RADAR_INGEST_MANIFEST", filepath.Join(t.TempDir(), "missing.json"))
	if err := Run(context.Background(), bdpruntime.Runtime{}, nil); err == nil {
		t.Fatal("missing manifest accepted")
	}
}
