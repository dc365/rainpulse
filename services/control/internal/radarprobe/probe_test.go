package radarprobe

import (
	"bytes"
	"compress/zlib"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"math"
	"testing"
)

func TestExactMissingAndBounds(t *testing.T) {
	values := make([]byte, 16)
	binary.LittleEndian.PutUint64(values, math.Float64bits(-8.25))
	binary.LittleEndian.PutUint64(values[8:], math.Float64bits(math.NaN()))
	var compressed bytes.Buffer
	writer := zlib.NewWriter(&compressed)
	_, _ = writer.Write(values)
	_ = writer.Close()
	raw, _ := json.Marshal(map[string]any{"encoding": "zlib-f64le", "width": 2, "height": 1, "fields": []string{"DBZH_RAW"}, "data": base64.StdEncoding.EncodeToString(compressed.Bytes())})
	hash := sha256.Sum256(raw)
	index := Index{Contract: "rainpulse.radar-probe-v1", Width: 2, Height: 1, TileSize: 64, Fields: []string{"DBZH_RAW"}, RowOrder: "north_to_south", Tiles: map[string]Tile{"0_0": {Path: "query/test.json", SHA256: hex.EncodeToString(hash[:])}}}
	read := func(context.Context, string) ([]byte, error) { return raw, nil }
	first, err := Sample(context.Background(), index, .1, .1, read)
	if err != nil {
		t.Fatal(err)
	}
	if first["values"].(map[string]any)["DBZH_RAW"] != -8.25 {
		t.Fatal(first)
	}
	second, err := Sample(context.Background(), index, .9, .1, read)
	if err != nil || second["values"].(map[string]any)["DBZH_RAW"] != nil {
		t.Fatal(second, err)
	}
	outside, err := Sample(context.Background(), index, 1, .1, read)
	if err != nil || outside["status"] != "outside" {
		t.Fatal(outside, err)
	}
	index.Tiles["0_0"] = Tile{Path: "../secret", SHA256: hex.EncodeToString(hash[:])}
	if _, err = Sample(context.Background(), index, .1, .1, read); err == nil {
		t.Fatal("unsafe key accepted")
	}
}

func TestFiniteExperimentalValueRetainsUncertainty(t *testing.T) {
	values := make([]byte, 16)
	binary.LittleEndian.PutUint64(values, math.Float64bits(25))
	binary.LittleEndian.PutUint64(values[8:], math.Float64bits(1))
	var compressed bytes.Buffer
	writer := zlib.NewWriter(&compressed)
	_, _ = writer.Write(values)
	_ = writer.Close()
	fields := []string{"CR_DBZH", "UNCERTAIN_MASK"}
	raw, _ := json.Marshal(map[string]any{"encoding": "zlib-f64le", "width": 1, "height": 1, "fields": fields, "data": base64.StdEncoding.EncodeToString(compressed.Bytes())})
	hash := sha256.Sum256(raw)
	index := Index{Contract: "rainpulse.radar-probe-v1", Width: 1, Height: 1, TileSize: 64, Fields: fields, RowOrder: "north_to_south", Tiles: map[string]Tile{"0_0": {Path: "query/test.json", SHA256: hex.EncodeToString(hash[:])}}}
	result, err := Sample(context.Background(), index, .5, .5, func(context.Context, string) ([]byte, error) { return raw, nil })
	if err != nil || result["state"] != "low_quality" || result["values"].(map[string]any)["CR_DBZH"] != float64(25) {
		t.Fatalf("%+v %v", result, err)
	}
}
