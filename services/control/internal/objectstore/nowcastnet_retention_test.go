package objectstore

import "testing"

func TestNowcastNetDeletionScope(t *testing.T) {
	good := "s3://rainpulse/products/97000000-0000-4000-8000-000000000001/nowcastnet/model/config/nowcastnet-shadow-products"
	if _, err := nowcastNetDeletionPrefix(good); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{"s3://rainpulse", "s3://rainpulse/products", "s3://rainpulse/radar/raw", good + "/..", good + "?x=1", "s3://other/products/x", good + "/%2e%2e"} {
		if _, err := nowcastNetDeletionPrefix(raw); err == nil {
			t.Fatalf("accepted unsafe URI %s", raw)
		}
	}
}
