package buildinfo

import "testing"

func TestIdentityIncludesDistinctRevision(t *testing.T) {
	originalVersion, originalRevision := Version, Revision
	t.Cleanup(func() { Version, Revision = originalVersion, originalRevision })
	Version, Revision = "rp019", "abc123"
	if got := Identity(); got != "rp019+abc123" {
		t.Fatalf("identity = %q", got)
	}
}
