//go:build ruiyun_bdp

package bdpruntime

import (
	"os"
	"strings"

	"bdp-publiccode-common/programinfo"
	"bdp-publiccode-common/systeminit"
	"bdp-publiccode-puremanage/purelog/runninglog"
	"bdp-publiccode-puremanage/pureprogram"
)

func NewProgramInfo(version string) *programinfo.ProgramInfo {
	return &programinfo.ProgramInfo{
		ProgramUniqueCode: ProgramUniqueCode,
		ProgramUnifyCode:  ProgramUniqueCode,
		ProgramZhName:     "雷达降水短临预报",
		ProgramVersion:    strings.TrimSpace(version),
		ProgramUsage:      "雷达基数据接入、质量控制、拼图、定量降水估测与短临外推",
		ProgramType:       programinfo.ProgramType_DataProcessing,
		ProgramClass:      "雷达气象",
		ProgramRunType:    programinfo.ProgramRunType_Time_Message,
		ProgramDevLang:    programinfo.ProgramDevLang_GoAndPython,
		IsBaseModule:      false,
		PlatformCode:      "dp",
	}
}

func initializeProgram(version, configCode string) {
	originalArgs := os.Args
	os.Args = withDefaultProgramName(originalArgs, configCode)
	defer func() { os.Args = originalArgs }()

	systeminit.Init(NewProgramInfo(version))
	pureprogram.UpdateHeartBeat()
	pureprogram.StartProgramMonitor()
	pureprogram.StartProgramRunningLogClean()
	runninglog.Info("main", "-------------RainPulse 程序预处理开始-------------")
}

func withDefaultProgramName(args []string, configCode string) []string {
	for _, arg := range args {
		lower := strings.ToLower(arg)
		if strings.Contains(lower, "programname:") || strings.Contains(lower, "link:") {
			return args
		}
	}
	result := append([]string(nil), args...)
	return append(result, "programname:"+ResolveConfigCode(configCode, ""))
}

func platformProgramName() string {
	if systeminit.ProgramRun == nil {
		return ""
	}
	return strings.TrimSpace(systeminit.ProgramRun.ProgramName)
}
