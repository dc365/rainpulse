package operations

import (
	"encoding/json"
	"regexp"
	"strings"
)

var producerRevisionPattern = regexp.MustCompile(`^python-source-tree-v1:[a-f0-9]{64}$`)

// This freezes a producer declaration in a committed marker. It does not prove
// that the actual input attributes/arrays match; the compute reader owns that.
func markerQCIdentity(m Marker, ref AssetRef) (*UpstreamQCIdentity, error) {
	if len(m.Completion) == 0 {
		return nil, nil
	}
	var event struct {
		EventType string `json:"event_type"`
		Payload   struct {
			Status string `json:"status"`
			Assets []struct {
				URI       string `json:"uri"`
				SHA256    string `json:"sha256"`
				SizeBytes int64  `json:"size_bytes"`
				AssetType string `json:"asset_type"`
			} `json:"assets"`
			Diagnostics map[string]json.RawMessage `json:"diagnostics"`
		} `json:"payload"`
	}
	if json.Unmarshal(m.Completion, &event) != nil {
		return nil, Conflict("输入完成事件无效")
	}
	raw, reported := event.Payload.Diagnostics["radar_qc"]
	if !reported {
		return nil, nil
	}
	if event.EventType != "job.completed" || event.Payload.Status != "succeeded" || len(event.Payload.Assets) != 1 {
		return nil, Conflict("上游QC完成事件或资产关联无效")
	}
	asset := event.Payload.Assets[0]
	if asset.AssetType != "qc_radar_volume" || asset.URI != ref.URI || asset.SHA256 != ref.SHA256 || asset.SizeBytes != ref.SizeBytes {
		return nil, Conflict("上游QC身份不属于此冻结资产")
	}
	var qc struct {
		RadarID    string            `json:"radar_id"`
		ScanID     string            `json:"scan_id"`
		Pipeline   string            `json:"qc_pipeline_version"`
		Parameters string            `json:"parameters_hash"`
		Revision   string            `json:"implementation_revision"`
		Profile    string            `json:"qc_profile"`
		Libraries  map[string]string `json:"libraries"`
	}
	if len(raw) > 64*1024 || json.Unmarshal(raw, &qc) != nil || !namePattern.MatchString(qc.RadarID) || !ValidID(qc.ScanID) || qc.Pipeline == "" || len(qc.Pipeline) > 512 || qc.Profile == "" || len(qc.Profile) > 512 || (qc.Parameters != "" && !shaPattern.MatchString(qc.Parameters)) || (qc.Revision != "" && !producerRevisionPattern.MatchString(qc.Revision)) || len(qc.Libraries) > 32 {
		return nil, Conflict("上游QC声明字段无效")
	}
	for name, version := range qc.Libraries {
		if len([]rune(name)) < 1 || len([]rune(name)) > 128 || len([]rune(version)) < 1 || len([]rune(version)) > 128 {
			return nil, Conflict("上游QC库版本声明无效")
		}
	}
	return &UpstreamQCIdentity{RadarID: strings.ToLower(qc.RadarID), ScanID: qc.ScanID,
		PipelineVersion: qc.Pipeline, ParametersSHA256: qc.Parameters, ImplementationRevision: qc.Revision,
		Profile: qc.Profile, Libraries: qc.Libraries}, nil
}
