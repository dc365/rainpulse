package workflow

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

type ForecastVerificationTruth struct {
	AnalysisID uuid.UUID
	ValidTime  time.Time
	URI        string
	SHA256     string
}

type ForecastVerificationBundle struct {
	Run                      Run
	ForecastURI              string
	ForecastSHA256           string
	Truth                    []ForecastVerificationTruth
	ForecastContractVersion  string
	ResultContractVersion    string
	VerificationConfig       json.RawMessage
	VerificationConfigSHA256 string
	Job                      Job
	Outbox                   OutboxEvent
}

type ForecastVerificationRecord struct {
	JobID          uuid.UUID
	RunID          uuid.UUID
	Status         string
	ProfileVersion string
	ResultURI      *string
	ResultSHA256   *string
	Summary        json.RawMessage
	StartedAt      *time.Time
	CompletedAt    *time.Time
	CreatedAt      time.Time
	UpdatedAt      time.Time
}

type ForecastVerificationHeadline struct {
	Band                  string              `json:"band"`
	ThresholdMMH          float64             `json:"threshold_mm_h"`
	FSSWindowTargetKM     float64             `json:"fss_window_target_km"`
	MeanFSS               map[string]*float64 `json:"mean_fss"`
	LKMinusPersistenceFSS *float64            `json:"lk_minus_persistence_fss"`
	LKMinusTranslationFSS *float64            `json:"lk_minus_translation_fss"`
}

type ForecastVerificationResultSummary struct {
	ContractName               string                       `json:"contract_name"`
	ContractVersion            string                       `json:"contract_version"`
	ProfileVersion             string                       `json:"profile_version"`
	RunID                      uuid.UUID                    `json:"run_id"`
	JobID                      uuid.UUID                    `json:"job_id"`
	ForecastURI                string                       `json:"forecast_uri"`
	ForecastContractVersion    string                       `json:"forecast_contract_version"`
	ModelID                    string                       `json:"model_id"`
	ModelVersion               string                       `json:"model_version"`
	IssueTime                  time.Time                    `json:"issue_time"`
	GridID                     string                       `json:"grid_id"`
	TruthKind                  string                       `json:"truth_kind"`
	TruthContractVersion       string                       `json:"truth_contract_version"`
	TruthFrameCount            int                          `json:"truth_frame_count"`
	TruthAnalysisIDs           []uuid.UUID                  `json:"truth_analysis_ids"`
	TruthURIs                  []string                     `json:"truth_uris"`
	LeadCount                  int                          `json:"lead_count"`
	LeadMinutes                []int                        `json:"lead_minutes"`
	Models                     []string                     `json:"models"`
	MetricRowCount             int                          `json:"metric_row_count"`
	AccumulationMetricRowCount int                          `json:"accumulation_metric_row_count"`
	NominalPixelSpacingKM      float64                      `json:"nominal_pixel_spacing_km"`
	ValidityDomain             string                       `json:"validity_domain"`
	TruthOperationalEligible   bool                         `json:"truth_operational_eligible"`
	PromotionEligible          bool                         `json:"promotion_eligible"`
	Headline                   ForecastVerificationHeadline `json:"headline"`
}

type ForecastVerificationStatus struct {
	RunID              uuid.UUID
	IssueTime          time.Time
	RunStatus          RunStatus
	Status             string
	TruthFrameCount    int
	MissingLeadMinutes []int
	ProfileVersion     *string
	ResultURI          *string
	ResultSHA256       *string
	Summary            *ForecastVerificationResultSummary
	VerifiedAt         *time.Time
}
