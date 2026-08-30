package postgres

import (
	"context"
	"strings"
	"testing"
)

func TestListRadarScansPageRejectsNegativeOffset(t *testing.T) {
	store := &Store{}
	_, err := store.ListRadarScansPage(context.Background(), 20, -1, nil, nil)
	if err == nil || !strings.Contains(err.Error(), "offset") {
		t.Fatalf("negative page offset error = %v", err)
	}
}
