package operations

import "encoding/json"

func qcRecipe(identity *UpstreamQCIdentity) map[string]any {
	if identity == nil {
		return nil
	}
	var recipe map[string]any
	_ = json.Unmarshal(JSON(identity), &recipe)
	delete(recipe, "radar_id")
	delete(recipe, "scan_id")
	return recipe
}

// Freeze across the whole bounded preflight, so one missing frame does not
// change the expected producer policy. No scan or asset IDs enter that policy.
func freezeSQCPolicy(specs []Spec, refs map[string]AssetRef) []Check {
	type decoded struct {
		index   int
		request map[string]any
		payload map[string]any
		sources []any
	}
	items := []decoded{}
	stations := map[string]bool{}
	unknown := map[string]bool{}
	policy := map[string]any{}
	checks := []Check{}
	for i, spec := range specs {
		var request map[string]any
		if json.Unmarshal(spec.Request, &request) != nil {
			continue
		}
		payload, ok := request["payload"].(map[string]any)
		if !ok || payload["mode"] != "sx_composite" {
			continue
		}
		radars, reported := payload["requested_s_radars"].([]any)
		if !reported {
			continue
		} // old plans
		for _, value := range radars {
			if radar, ok := value.(string); ok {
				stations[radar] = true
			}
		}
		sources, _ := payload["sources"].([]any)
		items = append(items, decoded{i, request, payload, sources})
	}
	for _, item := range items {
		for _, value := range item.sources {
			source, ok := value.(map[string]any)
			if !ok {
				continue
			}
			radar, _ := source["radar_id"].(string)
			if !stations[radar] {
				continue
			}
			uri, _ := source["input_uri"].(string)
			identity := refs[uri].QCIdentity
			if identity == nil {
				unknown[radar] = true
				continue
			}
			if identity.RadarID != radar || identity.ScanID != source["scan_id"] {
				checks = append(checks, Check{"upstream_qc_identity", "BLOCK", "上游QC声明不属于所选站点或体扫", specs[item.index].ID})
				continue
			}
			recipe := qcRecipe(identity)
			if prior, exists := policy[radar]; exists && CanonicalDigest(prior) != CanonicalDigest(recipe) {
				checks = append(checks, Check{"upstream_qc_identity", "BLOCK", "同一预检窗口含不同S质控身份，请分开选择窗口", radar})
				continue
			}
			policy[radar] = recipe
			source["expected_qc_identity"] = identity
		}
	}
	complete := true
	for radar := range stations {
		recipe, exists := policy[radar]
		if !exists || unknown[radar] {
			policy[radar] = nil
			complete = false
			continue
		}
		values := recipe.(map[string]any)
		for _, key := range []string{"pipeline_version", "parameters_sha256", "implementation_revision", "profile", "libraries"} {
			if value, ok := values[key]; !ok || value == nil {
				complete = false
			}
		}
	}
	for _, item := range items {
		item.payload["s_qc_policy"] = policy
		item.payload["s_qc_policy_complete"] = complete
		specs[item.index].Request = JSON(item.request)
	}
	if len(items) > 0 && !complete {
		checks = append(checks, Check{"upstream_qc_identity", "WARN", "上游S质控身份未完整记录；保留未知并按运行批次隔离系列", ""})
	}
	return checks
}
