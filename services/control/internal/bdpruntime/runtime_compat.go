//go:build !ruiyun_bdp

package bdpruntime

import (
	"fmt"
	"os"
)

func Prepare(_ Component, _ bool) (Runtime, error) {
	mode, err := modeFromEnvironment()
	if err != nil {
		return Runtime{}, err
	}
	runtime := Runtime{
		Mode:       mode,
		ConfigCode: ResolveConfigCode(os.Getenv("RAINPULSE_BDP_CONFIG_CODE"), ""),
		Config:     DefaultProgramConfig(),
	}
	if mode == ModeRequired {
		return Runtime{}, fmt.Errorf("Ruiyun BDP mode is required but this binary was built without the ruiyun_bdp tag")
	}
	return runtime, nil
}
