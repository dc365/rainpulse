//go:build ruiyun_bdp

package bdpruntime

import "testing"

func TestProgramInfoUsesRuiyunIdentity(t *testing.T) {
	info := NewProgramInfo("test-version")
	if info.ProgramUniqueCode != ProgramUniqueCode || info.ProgramUnifyCode != ProgramUniqueCode {
		t.Fatalf("program identity differs: %#v", info)
	}
	if info.PlatformCode != "dp" || info.ProgramVersion != "test-version" {
		t.Fatalf("program metadata differs: %#v", info)
	}
}
