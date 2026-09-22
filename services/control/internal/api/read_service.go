package api

import "github.com/fonwee/rainpulse-nowcast/services/control/internal/readquery"

// The constructor injects a shared immutable service. The fallback preserves
// package-local tests and legacy constructors without a lazy mutable singleton.
func (s *server) readService() *readquery.Service {
	if s.queries != nil {
		return s.queries
	}
	return readquery.New(s.runs, s.observations)
}
