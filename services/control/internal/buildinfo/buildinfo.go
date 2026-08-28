package buildinfo

import "strings"

var (
	Version  = "dev"
	Revision = "unknown"
)

func Identity() string {
	version := strings.TrimSpace(Version)
	revision := strings.TrimSpace(Revision)
	if version == "" {
		version = "dev"
	}
	if revision == "" || revision == "unknown" || revision == version {
		return version
	}
	return version + "+" + revision
}
