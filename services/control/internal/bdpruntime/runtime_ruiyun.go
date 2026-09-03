//go:build ruiyun_bdp

package bdpruntime

import (
	"fmt"
	"log/slog"
	"os"
	"strings"

	commonconfig "bdp-publiccode-common/config"
	programconfig "bdp-publiccode-puremanage/pureconfig/program"
	"bdp-publiccode-puremanage/purelog/runninglog"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo"
)

func Prepare(component Component, registerProgram bool) (Runtime, error) {
	mode, err := modeFromEnvironment()
	if err != nil {
		return Runtime{}, err
	}
	runtime := Runtime{
		Mode:       mode,
		ConfigCode: ResolveConfigCode(os.Getenv("RAINPULSE_BDP_CONFIG_CODE"), ""),
		Config:     DefaultProgramConfig(),
	}
	if mode == ModeOff {
		return runtime, nil
	}

	center := commonconfig.GetConfigCenter()
	if center == nil || strings.TrimSpace(center.ConfigDbConstr) == "" {
		if mode == ModeRequired {
			return Runtime{}, fmt.Errorf("Ruiyun BDP config center is required but unavailable")
		}
		return runtime, nil
	}
	runtime.PlatformAvailable = true
	if registerProgram {
		initializeProgram(buildinfo.Identity(), runtime.ConfigCode)
		runtime.ConfigCode = ResolveConfigCode(os.Getenv("RAINPULSE_BDP_CONFIG_CODE"), platformProgramName())
	}

	config, err := loadProgramConfig(runtime.ConfigCode, programconfig.GetConfigContent)
	if err != nil {
		if mode == ModeRequired {
			return Runtime{}, fmt.Errorf("load Ruiyun BDP ProgramConfig %q: %w", runtime.ConfigCode, err)
		}
		slog.Warn("Ruiyun BDP ProgramConfig unavailable; retaining deployment defaults",
			"config_code", runtime.ConfigCode, "error", err)
		return runtime, nil
	}
	applied, err := config.Apply(component)
	if err != nil {
		return Runtime{}, err
	}
	runtime.Config = config
	runtime.ConfigLoaded = true
	slog.Info("Ruiyun BDP ProgramConfig applied", "config_code", runtime.ConfigCode,
		"component", component, "environment_keys", applied)
	if registerProgram {
		runninglog.Info("main", fmt.Sprintf("RainPulse 平台配置加载完成：配置编码=%s，组件=%s", runtime.ConfigCode, component))
	}
	return runtime, nil
}
