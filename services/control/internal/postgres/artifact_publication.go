package postgres

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
)

func artifactDataURI(
	asset orchestration.JobCompletedAsset,
	diagnostics map[string]json.RawMessage,
) (string, error) {
	raw, exists := diagnostics["artifact_publication"]
	if !exists {
		// Publication markers written before schema 2.0 stored objects directly
		// below the stable artifact URI.
		return strings.TrimRight(asset.URI, "/"), nil
	}
	var publication struct {
		SchemaVersion string `json:"schema_version"`
		DataPrefix    string `json:"data_prefix"`
	}
	if err := json.Unmarshal(raw, &publication); err != nil {
		return "", fmt.Errorf("decode artifact publication metadata: %w", err)
	}
	expectedPrefix := "_objects/" + asset.SHA256
	if publication.SchemaVersion != "2.0" || publication.DataPrefix != expectedPrefix {
		return "", fmt.Errorf("artifact publication metadata differs from the committed asset")
	}
	return strings.TrimRight(asset.URI, "/") + "/" + expectedPrefix, nil
}
