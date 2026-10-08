package radarprobe

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"math"
	"os"
	"testing"
)

type sxGolden struct {
	Manifest json.RawMessage `json:"manifest"`
	Index    Index           `json:"index"`
	X, Y     float64
	Tiles    map[string]string `json:"tiles"`
}

func sxFixture(t *testing.T) (sxGolden, map[string]any) {
	t.Helper()
	raw, e := os.ReadFile("testdata/sx_finish_probe.json")
	if e != nil {
		t.Fatal(e)
	}
	var f sxGolden
	if e = json.Unmarshal(raw, &f); e != nil {
		t.Fatal(e)
	}
	result, e := Sample(context.Background(), f.Index, f.X, f.Y, func(ctx context.Context, key string) ([]byte, error) {
		return base64.StdEncoding.DecodeString(f.Tiles[key])
	})
	if e != nil {
		t.Fatal(e)
	}
	return f, result
}
func TestSXFinishPythonWriterGoProbe(t *testing.T) {
	f, r := sxFixture(t)
	if len(f.Index.Fields) != 23 {
		t.Fatal("fixture does not exercise old 16-field failure")
	}
	out, e := BindCompositeSource(r, f.Manifest, "x_only")
	if e != nil {
		t.Fatal(e)
	}
	s := out["source"].(map[string]any)
	if s["radar_id"] != "x1" || s["band"] != "X" || s["sweep_number"] != 2 || s["ray"] != 3 || s["gate"] != 5 {
		t.Fatalf("wrong own-product source: %#v", s)
	}
	if _, ok := s["input_uri"]; ok {
		t.Fatal("private object URI leaked")
	}
}
func TestSXFinishBadNativeIntegers(t *testing.T) {
	for _, bad := range []any{nil, "1", float64(-1), .5, math.NaN(), math.Inf(1), float64(4096)} {
		t.Run("invalid", func(t *testing.T) {
			f, r := sxFixture(t)
			r["values"].(map[string]any)["WINNER_RAY"] = bad
			if _, e := BindCompositeSource(r, f.Manifest, "x_only"); e == nil {
				t.Fatal("bad native identity accepted")
			}
		})
	}
}
func TestSXFinishWrongProductOrTime(t *testing.T) {
	for _, key := range []string{"product_id", "analysis_time", "sources_sha256"} {
		t.Run(key, func(t *testing.T) {
			f, r := sxFixture(t)
			r["identity"].(map[string]any)[key] = "wrong"
			if _, e := BindCompositeSource(r, f.Manifest, "x_only"); e == nil {
				t.Fatal("identity mismatch accepted")
			}
		})
	}
}
func TestSXFinishChangedSourceTable(t *testing.T) {
	f, r := sxFixture(t)
	var m map[string]any
	json.Unmarshal(f.Manifest, &m)
	for _, p := range m["comparison"].(map[string]any)["products"].([]any) {
		x := p.(map[string]any)
		if x["product_id"] == "x_only" {
			x["provenance"].(map[string]any)["sources"].([]any)[0].(map[string]any)["radar_id"] = "changed"
		}
	}
	raw, _ := json.Marshal(m)
	if _, e := BindCompositeSource(r, raw, "x_only"); e == nil {
		t.Fatal("changed table accepted")
	}
}
func TestSXFinishLegacyAndAbsentEcho(t *testing.T) {
	f, r := sxFixture(t)
	r["values"].(map[string]any)["CR_DBZH"] = nil
	out, e := BindCompositeSource(r, f.Manifest, "x_only")
	if e != nil || out["provenance_status"] != "no_echo_winner" {
		t.Fatal(out, e)
	}
	_, r = sxFixture(t)
	out, e = BindCompositeSource(r, []byte(`{"comparison":{"products":[{"product_id":"x_only"}]}}`), "x_only")
	if e != nil || out["provenance_status"] != "legacy_unresolved" {
		t.Fatal(out, e)
	}
}
func TestSXFinishProbeLimitsAndChecksum(t *testing.T) {
	for _, change := range []string{"fields", "checksum", "outside"} {
		t.Run(change, func(t *testing.T) {
			f, _ := sxFixture(t)
			if change == "fields" {
				f.Index.Fields = make([]string, 25)
			}
			if change == "checksum" {
				for k, v := range f.Index.Tiles {
					v.SHA256 = "bad"
					f.Index.Tiles[k] = v
				}
			}
			if change == "outside" {
				f.X = 1.0
			}
			r, e := Sample(context.Background(), f.Index, f.X, f.Y, func(ctx context.Context, key string) ([]byte, error) {
				return base64.StdEncoding.DecodeString(f.Tiles[key])
			})
			if change == "outside" {
				if e != nil || r["status"] != "outside" {
					t.Fatal(r, e)
				}
			} else if e == nil {
				t.Fatal("malformed probe accepted")
			}
		})
	}
}
