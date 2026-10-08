package radarprobe

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
)

const CompositeSourceContract = "rainpulse.sx-source-v2"

// BindCompositeSource resolves only the selected product's explicit namespace.
// Old manifests remain scalar-readable but are never assigned guessed sources.
func BindCompositeSource(result map[string]any, raw []byte, productID string) (map[string]any, error) {
	if result["status"] != "available" {
		return result, nil
	}
	if len(raw) > 16*1024*1024 {
		return nil, fmt.Errorf("composite manifest too large")
	}
	var manifest struct {
		AnalysisTime string `json:"analysis_time"`
		Comparison   struct {
			Products []struct {
				ProductID  string `json:"product_id"`
				Provenance *struct {
					Contract   string          `json:"contract"`
					SourcesRaw json.RawMessage `json:"sources"`
					Indices    []int           `json:"source_indices"`
					SourcesSHA string          `json:"sources_sha256"`
				} `json:"provenance"`
			} `json:"products"`
		} `json:"comparison"`
	}
	if json.Unmarshal(raw, &manifest) != nil {
		return nil, fmt.Errorf("invalid composite manifest")
	}
	if productID != "s_only" && productID != "x_only" && productID != "sx_composite" {
		result["provenance_status"] = "scalar_diagnostic"
		return result, nil
	}
	for _, p := range manifest.Comparison.Products {
		if p.ProductID != productID {
			continue
		}
		if p.Provenance == nil || p.Provenance.Contract != CompositeSourceContract {
			result["provenance_status"] = "legacy_unresolved"
			return result, nil
		}
		prov := p.Provenance
		var sources []map[string]any
		if json.Unmarshal(prov.SourcesRaw, &sources) != nil {
			return nil, fmt.Errorf("invalid source table")
		}
		var compact bytes.Buffer
		if json.Compact(&compact, prov.SourcesRaw) != nil {
			return nil, fmt.Errorf("invalid canonical source table")
		}
		hash := sha256.Sum256(compact.Bytes())
		if hex.EncodeToString(hash[:]) != prov.SourcesSHA {
			return nil, fmt.Errorf("source table checksum differs")
		}
		if len(sources) > 4096 || len(prov.Indices) > 4096 {
			return nil, fmt.Errorf("source table exceeds budget")
		}
		identity, ok := result["identity"].(map[string]any)
		if !ok || identity["product_id"] != productID || identity["analysis_time"] != manifest.AnalysisTime || identity["source_contract"] != CompositeSourceContract || identity["sources_sha256"] != prov.SourcesSHA || len(prov.SourcesSHA) != 64 {
			return nil, fmt.Errorf("probe/product source identity differs")
		}
		values, ok := result["values"].(map[string]any)
		if !ok {
			return nil, fmt.Errorf("missing numeric probe")
		}
		if v, exists := values["CR_DBZH"]; !exists || v == nil {
			result["provenance_status"] = "no_echo_winner"
			return result, nil
		}
		sourceIndex, e := sourceInteger(values["WINNER_SOURCE"], 4095)
		if e != nil {
			return nil, e
		}
		allowed := false
		seen := map[int]bool{}
		for _, i := range prov.Indices {
			if i < 0 || i >= len(sources) || seen[i] {
				return nil, fmt.Errorf("invalid product source subset")
			}
			seen[i] = true
			if i == sourceIndex {
				allowed = true
			}
		}
		if !allowed {
			return nil, fmt.Errorf("winner outside product source namespace")
		}
		for i, s := range sources {
			index, err := sourceInteger(s["index"], 4095)
			if err != nil || index != i {
				return nil, fmt.Errorf("source table is not explicitly indexed")
			}
		}
		s := sources[sourceIndex]
		band, _ := s["band"].(string)
		if (productID == "s_only" && band != "S") || (productID == "x_only" && band != "X") || (band != "S" && band != "X") {
			return nil, fmt.Errorf("winner band differs from product")
		}
		ray, e := sourceInteger(values["WINNER_RAY"], 4095)
		if e != nil {
			return nil, e
		}
		gate, e := sourceInteger(values["WINNER_GATE"], 16383)
		if e != nil {
			return nil, e
		}
		sweep, e := sourceInteger(values["WINNER_SWEEP_NUMBER"], 4096)
		if e != nil {
			return nil, e
		}
		switch s["source_granularity"] {
		case "native_cut":
			cut, err := sourceInteger(s["sweep_number"], 4096)
			if err != nil || cut != sweep {
				return nil, fmt.Errorf("winner cut disagrees with source")
			}
		case "native_volume":
			if rawCuts, exists := s["sweep_numbers"]; exists {
				cuts, ok := rawCuts.([]any)
				if !ok {
					return nil, fmt.Errorf("invalid native cut inventory")
				}
				found := false
				for _, v := range cuts {
					n, err := sourceInteger(v, 4096)
					if err != nil {
						return nil, err
					}
					if n == sweep {
						found = true
					}
				}
				if !found {
					return nil, fmt.Errorf("winner cut outside native volume")
				}
			}
		default:
			return nil, fmt.Errorf("unknown source granularity")
		}
		radar, rok := s["radar_id"].(string)
		scan, sok := s["scan_id"].(string)
		if !rok || !sok || radar == "" || scan == "" {
			return nil, fmt.Errorf("incomplete native source identity")
		}
		source := map[string]any{"index": sourceIndex, "radar_id": radar, "scan_id": scan, "band": band, "sweep_number": sweep, "ray": ray, "gate": gate}
		// Deliberate allowlist: do not expose private input object URIs.
		for _, key := range []string{"asset_sha256", "volume_end", "available_at", "qc_version", "calibration_id", "network_sha256"} {
			if v, ok := s[key]; ok {
				source[key] = v
			}
		}
		result["source"] = source
		result["provenance_status"] = "resolved"
		return result, nil
	}
	return nil, fmt.Errorf("unknown comparison product")
}

func sourceInteger(v any, maximum int) (int, error) {
	n, ok := v.(float64)
	if !ok || math.IsNaN(n) || math.IsInf(n, 0) || n < 0 || n > float64(maximum) || n != math.Trunc(n) {
		return 0, fmt.Errorf("invalid native source integer")
	}
	return int(n), nil
}
