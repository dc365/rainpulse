// Package multiband defines bounded S/X product plans, independent of HTTP and
// numerical workers. A cadence belongs to a product, not to every radar.
package multiband

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"
)

var namePattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}$`)

type Station struct {
	Band                string          `json:"band"`
	Frequency           float64         `json:"frequency_hz"`
	Longitude           float64         `json:"longitude_deg"`
	Latitude            float64         `json:"latitude_deg"`
	Altitude            float64         `json:"altitude_m_msl"`
	BeamH               float64         `json:"beam_width_h_deg"`
	BeamV               float64         `json:"beam_width_v_deg"`
	Source              string          `json:"source"`
	Enabled             bool            `json:"enabled"`
	GeometryVerified    bool            `json:"geometry_verified"`
	CalibrationVerified bool            `json:"calibration_verified"`
	CalibrationID       string          `json:"calibration_id"`
	Cadence             int             `json:"nominal_cadence_seconds"`
	MaximumAge          int             `json:"maximum_age_seconds"`
	QualityScale        float64         `json:"quality_scale"`
	AllowedSQCVersions  []string        `json:"allowed_s_qc_versions"`
	XQC                 json.RawMessage `json:"x_qc"`
}
type Grid struct {
	ID       string    `json:"grid_id"`
	CRS      string    `json:"crs"`
	West     float64   `json:"west_m"`
	South    float64   `json:"south_m"`
	Spacing  float64   `json:"spacing_m"`
	Width    int       `json:"width"`
	Height   int       `json:"height"`
	Levels   []float64 `json:"levels_m_msl"`
	TileRows int       `json:"tile_rows,omitempty"`
	Cadence  int       `json:"cadence_seconds,omitempty"`
}
type Network struct {
	Schema            string             `json:"schema_version"`
	Release           string             `json:"release_id"`
	Stations          map[string]Station `json:"stations"`
	Products          map[string]Grid    `json:"products"`
	CacheMaxBytes     int64              `json:"cache_max_bytes"`
	CacheTTL          int                `json:"cache_ttl_seconds"`
	MaximumInputBytes int64              `json:"maximum_input_bytes"`
	SHA256            string             `json:"-"`
}

func Parse(raw []byte) (Network, error) {
	var n Network
	if len(raw) > 1<<20 {
		return n, fmt.Errorf("network file exceeds 1 MiB")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if e := d.Decode(&n); e != nil {
		return n, e
	}
	if e := d.Decode(new(any)); e != io.EOF {
		return n, fmt.Errorf("trailing network JSON")
	}
	if n.Schema != "1.0" || !namePattern.MatchString(n.Release) || len(n.Stations) < 1 || len(n.Stations) > 16 || len(n.Products) < 1 || len(n.Products) > 4 {
		return n, fmt.Errorf("invalid network identity/inventory")
	}
	for id, s := range n.Stations {
		if !namePattern.MatchString(id) || strings.ToLower(id) != id || (s.Band != "S" && s.Band != "X") {
			return n, fmt.Errorf("invalid station %s", id)
		}
		if (s.Band == "S" && (s.Frequency < 2e9 || s.Frequency > 4e9)) || (s.Band == "X" && (s.Frequency < 8e9 || s.Frequency > 12e9)) {
			return n, fmt.Errorf("station band/frequency differs")
		}
		if s.Source != "s_qc_zarr" && s.Source != "normalized_zarr" && s.Source != "native_bundle" {
			return n, fmt.Errorf("unsupported input adapter")
		}
		if s.Source == "s_qc_zarr" && s.Band != "S" || s.Source == "normalized_zarr" && s.Band != "X" {
			return n, fmt.Errorf("adapter and band differ")
		}
		if s.Cadence == 0 {
			s.Cadence = 60
		}
		if s.MaximumAge == 0 {
			s.MaximumAge = 120
		}
		if s.QualityScale == 0 {
			s.QualityScale = 1
		}
		if s.Cadence < 1 || s.Cadence > 3600 || s.MaximumAge < 1 || s.MaximumAge > 3600 || s.Enabled && !s.GeometryVerified {
			return n, fmt.Errorf("station cadence/geometry not verified")
		}
		if s.Longitude < -180 || s.Longitude > 180 || s.Latitude <= -85 || s.Latitude >= 85 || s.Altitude < -500 || s.Altitude > 9000 || s.BeamH <= 0 || s.BeamH > 5 || s.BeamV <= 0 || s.BeamV > 5 || s.QualityScale <= 0 || s.QualityScale > 1 {
			return n, fmt.Errorf("station geometry/quality out of bounds")
		}
		n.Stations[id] = s
	}
	for id, g := range n.Products {
		if !namePattern.MatchString(id) || !namePattern.MatchString(g.ID) || g.CRS == "" || g.Spacing < 100 || g.Spacing > 5000 || g.Width < 1 || g.Width > 2048 || g.Height < 1 || g.Height > 2048 || g.Width*g.Height > 1000000 || len(g.Levels) < 1 || len(g.Levels) > 32 {
			return n, fmt.Errorf("product grid exceeds limits")
		}
		if g.Cadence == 0 {
			g.Cadence = 60
		}
		if g.TileRows == 0 {
			g.TileRows = 32
		}
		if (g.Cadence != 60 && g.Cadence != 360) || g.TileRows < 1 || g.TileRows > 128 {
			return n, fmt.Errorf("invalid product cadence/tile")
		}
		for i, v := range g.Levels {
			if math.IsNaN(v) || math.IsInf(v, 0) || v < -500 || v > 20000 || (i > 0 && v <= g.Levels[i-1]) {
				return n, fmt.Errorf("invalid MSL levels")
			}
		}
		n.Products[id] = g
	}
	h := sha256.Sum256(raw)
	n.SHA256 = hex.EncodeToString(h[:])
	return n, nil
}
func Load(path string) (Network, error) {
	f, e := os.Open(path)
	if e != nil {
		return Network{}, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if e != nil {
		return Network{}, e
	}
	return Parse(b)
}

type Scan struct {
	ID            string
	RadarID       string
	Start         time.Time
	End           time.Time
	AvailableAt   time.Time
	NormalizedURI string
	QCURI         string
}
type Input struct {
	RadarID     string    `json:"radar_id"`
	ScanID      string    `json:"scan_id"`
	InputURI    string    `json:"input_uri"`
	Start       time.Time `json:"volume_start"`
	End         time.Time `json:"volume_end"`
	AvailableAt time.Time `json:"available_at"`
}
type Snapshot struct {
	AnalysisTime time.Time
	Sources      []Input
	Slot         string
}

func Slot(product string, at time.Time) string {
	h := sha256.Sum256([]byte("rainpulse:sx-composite:" + product + ":" + at.UTC().Format(time.RFC3339Nano)))
	b := h[:16]
	b[6] = (b[6] & 15) | 128 // UUIDv8: application-defined SHA-256-derived identity
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b)
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}

// Select never interprets "X is faster" as "X is always better". It only freezes
// the latest available causal scan of each selected station. Numerical admission
// and same-height quality selection belong to the compute plane.
func Select(n Network, product, mode string, radars []string, start, end, cutoff time.Time, scans []Scan) ([]Snapshot, []string, error) {
	if mode != "x_qc" && mode != "sx_composite" {
		return nil, nil, fmt.Errorf("unsupported multiband mode")
	}
	if _, ok := n.Products[product]; !ok {
		return nil, nil, fmt.Errorf("unknown product")
	}
	cadence := time.Duration(n.Products[product].Cadence) * time.Second
	if !end.After(start) || end.Sub(start) > time.Hour || len(radars) < 1 || len(radars) > 16 || start.After(cutoff) || end.After(cutoff.Add(time.Minute)) {
		return nil, nil, fmt.Errorf("use a bounded, past one-hour window")
	}
	if !start.Equal(start.Truncate(cadence)) || !end.Equal(end.Truncate(cadence)) {
		return nil, nil, fmt.Errorf("product-cadence-aligned window required")
	}
	seen := map[string]bool{}
	for _, r := range radars {
		s, ok := n.Stations[r]
		if !ok || !s.Enabled || seen[r] || mode == "x_qc" && s.Band != "X" {
			return nil, nil, fmt.Errorf("disabled, duplicate or wrong-band station %s", r)
		}
		seen[r] = true
	}
	if len(scans) > 4096 {
		return nil, nil, fmt.Errorf("scan inventory exceeds budget")
	}
	ids := append([]string(nil), radars...)
	sort.Strings(ids)
	warnings := []string{}
	out := []Snapshot{}
	input := func(s Scan) (Input, bool) {
		station := n.Stations[s.RadarID]
		uri := s.NormalizedURI
		if station.Band == "S" {
			uri = s.QCURI
		}
		if uri == "" || s.Start.After(s.End) || s.AvailableAt.Before(s.End) || s.AvailableAt.After(cutoff) {
			return Input{}, false
		}
		return Input{s.RadarID, s.ID, uri, s.Start.UTC(), s.End.UTC(), s.AvailableAt.UTC()}, true
	}
	if mode == "x_qc" {
		seenIDs := map[string]bool{}
		ordered := append([]Scan(nil), scans...)
		sort.Slice(ordered, func(i, j int) bool {
			if ordered[i].End.Equal(ordered[j].End) {
				return ordered[i].ID < ordered[j].ID
			}
			return ordered[i].End.Before(ordered[j].End)
		})
		for _, s := range ordered {
			if !seen[s.RadarID] || seenIDs[s.ID] || s.End.Before(start) || !s.End.Before(end) {
				continue
			}
			v, ok := input(s)
			if !ok {
				warnings = append(warnings, "unusable X scan "+s.ID)
				continue
			}
			seenIDs[s.ID] = true
			// A scan ending between minute boundaries remains an actual observation;
			// its standalone quicklook is timestamped at the following product minute.
			at := s.End.Truncate(cadence)
			if !at.Equal(s.End) {
				at = at.Add(cadence)
			}
			if at.After(cutoff) {
				warnings = append(warnings, "X scan waits for next analysis minute "+s.ID)
				continue
			}
			out = append(out, Snapshot{at, []Input{v}, s.ID})
		}
	} else {
		for at := start; at.Before(end) && !at.After(cutoff); at = at.Add(cadence) {
			snap := Snapshot{AnalysisTime: at.UTC(), Sources: []Input{}, Slot: Slot(product, at)}
			for _, r := range ids {
				var best Scan
				found := false
				for _, s := range scans {
					if s.RadarID != r || s.End.After(at) || at.Sub(s.End) > time.Duration(n.Stations[r].MaximumAge)*time.Second {
						continue
					}
					if _, ok := input(s); !ok {
						continue
					}
					if !found || s.End.After(best.End) || (s.End.Equal(best.End) && s.ID < best.ID) {
						best = s
						found = true
					}
				}
				if found {
					v, _ := input(best)
					snap.Sources = append(snap.Sources, v)
				} else {
					warnings = append(warnings, at.Format(time.RFC3339)+" missing/expired "+r)
				}
			}
			if len(snap.Sources) > 0 {
				out = append(out, snap)
			}
		}
	}
	if len(out) > 64 {
		return nil, nil, fmt.Errorf("at most 64 product/scan tasks per plan")
	}
	return out, warnings, nil
}
