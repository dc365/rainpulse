package postgres

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
)

func TestArtifactDataURIResolvesContentAddressedAndLegacyLayouts(t *testing.T) {
	digest := strings.Repeat("a", 64)
	asset := orchestration.JobCompletedAsset{
		URI: "s3://rainpulse/products/run/application-products", SHA256: digest,
	}
	resolved, err := artifactDataURI(asset, map[string]json.RawMessage{
		"artifact_publication": json.RawMessage(
			`{"schema_version":"2.0","data_prefix":"_objects/` + digest + `"}`,
		),
	})
	if err != nil {
		t.Fatal(err)
	}
	want := asset.URI + "/_objects/" + digest
	if resolved != want {
		t.Fatalf("resolved URI = %q, want %q", resolved, want)
	}
	legacy, err := artifactDataURI(asset, nil)
	if err != nil || legacy != asset.URI {
		t.Fatalf("legacy URI = %q, err=%v", legacy, err)
	}
}

func TestArtifactDataURIRejectsPrefixThatDoesNotMatchAssetDigest(t *testing.T) {
	asset := orchestration.JobCompletedAsset{
		URI:    "s3://rainpulse/products/run/application-products",
		SHA256: strings.Repeat("a", 64),
	}
	_, err := artifactDataURI(asset, map[string]json.RawMessage{
		"artifact_publication": json.RawMessage(
			`{"schema_version":"2.0","data_prefix":"_objects/` + strings.Repeat("b", 64) + `"}`,
		),
	})
	if err == nil {
		t.Fatal("mismatched artifact publication prefix was accepted")
	}
}
