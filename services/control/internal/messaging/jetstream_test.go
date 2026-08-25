package messaging

import (
	"slices"
	"testing"
)

func TestJobStreamIncludesProductPublicationSubject(t *testing.T) {
	configuration := jobStreamConfiguration()
	want := []string{"rainpulse.jobs.>", "rainpulse.products.published"}
	if !slices.Equal(configuration.Subjects, want) {
		t.Fatalf("job stream subjects = %v, want %v", configuration.Subjects, want)
	}
}
