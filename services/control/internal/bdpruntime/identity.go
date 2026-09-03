package bdpruntime

const (
	ProgramUniqueCode = "bdp-dp-rada-rainpulse"
	DefaultConfigCode = ProgramUniqueCode
	DefaultDataCode   = "RADA_L2_FMT"
)

type OriginalFileSource struct {
	DataCode             string
	DataFormat           string
	SourceIndex          int
	SourceType           string
	CredentialConfigCode string
	FileSystemType       string
	Root                 string
}
