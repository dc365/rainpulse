// Package radarprobe decodes bounded, content-bound scalar tiles. No colour sampling.
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
	"fmt"
	"io"
	"math"
	"path"
)

type Tile struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}
type Index struct {
	Contract string          `json:"contract"`
	Width    int             `json:"width"`
	Height   int             `json:"height"`
	TileSize int             `json:"tile_size"`
	Fields   []string        `json:"fields"`
	RowOrder string          `json:"row_order"`
	ImageSHA string          `json:"image_sha256"`
	Identity map[string]any  `json:"identity"`
	Tiles    map[string]Tile `json:"tiles"`
}
type Read func(context.Context, string) ([]byte, error)

func Sample(ctx context.Context, index Index, x, y float64, read Read) (map[string]any, error) {
	if index.Contract != "rainpulse.radar-probe-v1" || index.TileSize != 64 || index.Width < 1 || index.Height < 1 || index.Width > 4096 || index.Height > 4096 || len(index.Fields) < 1 || len(index.Fields) > 24 || index.RowOrder != "north_to_south" {
		return nil, fmt.Errorf("numeric index unavailable")
	}
	if math.IsNaN(x) || math.IsNaN(y) || math.IsInf(x, 0) || math.IsInf(y, 0) || x < 0 || x >= 1 || y < 0 || y >= 1 {
		return map[string]any{"status": "outside"}, nil
	}
	col, row := int(x*float64(index.Width)), int(y*float64(index.Height))
	tile, ok := index.Tiles[fmt.Sprintf("%d_%d", row/64, col/64)]
	if !ok || path.Clean(tile.Path) != tile.Path || len(tile.Path) > 512 || len(tile.Path) < 6 || tile.Path[:6] != "query/" {
		return nil, fmt.Errorf("invalid tile identity")
	}
	raw, err := read(ctx, tile.Path)
	if err != nil {
		return nil, err
	}
	if len(raw) > 2*1024*1024 {
		return nil, fmt.Errorf("tile too large")
	}
	hash := sha256.Sum256(raw)
	if hex.EncodeToString(hash[:]) != tile.SHA256 {
		return nil, fmt.Errorf("tile checksum differs")
	}
	var data struct {
		Encoding string   `json:"encoding"`
		Width    int      `json:"width"`
		Height   int      `json:"height"`
		Fields   []string `json:"fields"`
		Data     string   `json:"data"`
	}
	if json.Unmarshal(raw, &data) != nil || data.Encoding != "zlib-f64le" || data.Width < 1 || data.Width > 64 || data.Height < 1 || data.Height > 64 || len(data.Fields) != len(index.Fields) {
		return nil, fmt.Errorf("invalid tile")
	}
	for i, name := range data.Fields {
		if name != index.Fields[i] {
			return nil, fmt.Errorf("tile fields differ")
		}
	}
	compressed, err := base64.StdEncoding.DecodeString(data.Data)
	if err != nil {
		return nil, err
	}
	reader, err := zlib.NewReader(bytes.NewReader(compressed))
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	expected := data.Width * data.Height * len(data.Fields) * 8
	values, err := io.ReadAll(io.LimitReader(reader, int64(expected+1)))
	if err != nil || len(values) != expected {
		return nil, fmt.Errorf("invalid tile length")
	}
	if row%64 >= data.Height || col%64 >= data.Width {
		return nil, fmt.Errorf("invalid tile edge")
	}
	offset := ((row%64)*data.Width + col%64) * len(data.Fields) * 8
	result := map[string]any{}
	for i, name := range data.Fields {
		v := math.Float64frombits(binary.LittleEndian.Uint64(values[offset+i*8 : offset+(i+1)*8]))
		if math.IsInf(v, 0) {
			return nil, fmt.Errorf("infinite scalar")
		}
		if math.IsNaN(v) {
			result[name] = nil
		} else {
			result[name] = v
		}
	}
	state := "valid"
	rawValue, hasRaw := result["DBZH_RAW"]
	cr, hasCR := result["CR_DBZH"]
	if !hasCR {
		for _, field := range []string{"CR_DBZH_S_ONLY", "CR_DBZH_X_ONLY"} {
			if value, exists := result[field]; exists {
				cr, hasCR = value, true
				break
			}
		}
	}
	if (hasRaw && rawValue == nil) || (hasCR && cr == nil) {
		state = "missing"
		if result["NO_ECHO_MASK"] == float64(1) {
			state = "no_echo"
		}
		if result["UNCERTAIN_MASK"] == float64(1) {
			state = "low_quality"
		}
	} else if result["UNCERTAIN_MASK"] == float64(1) || result["QC_ACTION"] == float64(3) || result["LOW_QUALITY_MASK"] == float64(1) {
		state = "low_quality"
	} else if result["DISPLAY_VALID"] == float64(0) {
		state = "rejected"
	}
	return map[string]any{"state": state, "status": "available", "values": result, "identity": index.Identity, "pixel_row": row, "pixel_column": col, "image_sha256": index.ImageSHA}, nil
}
