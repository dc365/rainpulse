//go:build !ruiyun_bdp

package bdpruntime

import "fmt"

func ResolveOriginalFileSource(dataCode string, _ int) (OriginalFileSource, error) {
	return OriginalFileSource{}, fmt.Errorf(
		"Ruiyun BDP metadata %s is unavailable in a binary built without the ruiyun_bdp tag",
		dataCode,
	)
}
