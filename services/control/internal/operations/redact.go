package operations

import (
	"encoding/json"
	"regexp"
	"strings"
)

var secretAssignment = regexp.MustCompile(`(?i)(password|passwd|secret|access_key|api_key|token|authorization)(["']?\s*[:=]\s*["']?)([^\s,"'}]+)`)
var bearer = regexp.MustCompile(`(?i)Bearer\s+[A-Za-z0-9._~+/=-]+`)
var urlCredentials = regexp.MustCompile(`(https?://|postgres(?:ql)?://)[^\s/@:]+:[^\s/@]+@`)

func Redact(s string) string {
	s = urlCredentials.ReplaceAllString(s, "${1}[REDACTED]@")
	s = bearer.ReplaceAllString(s, "Bearer [REDACTED]")
	s = secretAssignment.ReplaceAllString(s, "${1}${2}[REDACTED]")
	r := []rune(s)
	if len(r) > 8192 {
		s = string(r[:8192]) + " [truncated]"
	}
	return s
}
func RedactJSON(b []byte) []byte {
	var v any
	if json.Unmarshal(b, &v) != nil {
		return JSON(map[string]any{"unavailable": true})
	}
	var walk func(any) any
	walk = func(v any) any {
		switch x := v.(type) {
		case map[string]any:
			for k, item := range x {
				lower := strings.ToLower(k)
				if strings.Contains(lower, "password") || strings.Contains(lower, "token") || strings.Contains(lower, "secret") || lower == "authorization" {
					x[k] = "[REDACTED]"
				} else {
					x[k] = walk(item)
				}
			}
			return x
		case []any:
			for i, item := range x {
				x[i] = walk(item)
			}
			return x
		case string:
			return Redact(x)
		default:
			return v
		}
	}
	return JSON(walk(v))
}
