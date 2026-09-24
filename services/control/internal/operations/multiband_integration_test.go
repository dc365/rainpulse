//go:build integration

package operations

import (
	"context"
	"os"
	"testing"
)

func TestOpsPostgresMultiBandMigration(t *testing.T) {
	s := integrationStore(t)
	raw, e := os.ReadFile("schema_multiband_v1.sql")
	if e != nil {
		t.Fatal(e)
	}
	for i := 0; i < 2; i++ {
		if _, e = s.DB.ExecContext(context.Background(), string(raw)); e != nil {
			t.Fatal(e)
		}
	}
	var mode string
	if e = s.DB.QueryRow("SELECT mode FROM ops_pool_controls WHERE kind='multiband'").Scan(&mode); e != nil {
		t.Fatal(e)
	}
	if mode != "DRAINING" {
		t.Fatal("new compute lane must require explicit acceptance/resume")
	}
	if e = s.Register(context.Background(), WorkerInfo{ID: "multiband-test", Identity: identity("multiband"), Ready: true}); e != nil {
		t.Fatal(e)
	}
	var version int
	if e = s.DB.QueryRow("SELECT version FROM ops_schema WHERE version=4").Scan(&version); e != nil {
		t.Fatal(e)
	}
}
