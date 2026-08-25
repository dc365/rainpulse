package workflow

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

type ProductType string

const (
	ProductRainRate        ProductType = "rain_rate"
	ProductAccumulation60  ProductType = "accumulation_60"
	ProductAccumulation120 ProductType = "accumulation_120"
)

type ProductBuildBundle struct {
	Run                 Run
	ModelRunID          uuid.UUID
	ForecastURI         string
	ForecastSHA256      string
	InputAssetIDs       []uuid.UUID
	ProductIDs          map[ProductType]uuid.UUID
	ModelConfigVersion  string
	ProductConfig       json.RawMessage
	ProductConfigSHA256 string
	BundleContract      string
	Job                 Job
	Outbox              OutboxEvent
}

type Product struct {
	ID                   uuid.UUID
	RunID                uuid.UUID
	ModelRunID           uuid.UUID
	ProductType          ProductType
	ModelID              string
	ModelVersion         string
	ConfigVersion        string
	GridID               string
	IssueTime            time.Time
	ValidTimes           []time.Time
	MemberCount          int
	SourceForecastURI    string
	SourceForecastSHA256 string
	Metadata             json.RawMessage
	CreatedAt            time.Time
}

type ProductAsset struct {
	ID          uuid.UUID
	ProductID   uuid.UUID
	AssetType   string
	ObjectURI   string
	MediaType   string
	SHA256      string
	SizeBytes   int64
	LeadMinutes *int
	ValidTime   *time.Time
	Metadata    json.RawMessage
	CreatedAt   time.Time
}

type ProductAssetManifest struct {
	ObjectPath       string          `json:"object_path"`
	AssetType        string          `json:"asset_type"`
	MediaType        string          `json:"media_type"`
	SHA256           string          `json:"sha256"`
	SizeBytes        int64           `json:"size_bytes"`
	LeadMinutes      *int            `json:"lead_time_minutes"`
	ValidTime        *time.Time      `json:"valid_time"`
	Unit             string          `json:"unit"`
	CellCount        *int64          `json:"cell_count,omitempty"`
	ValidCellCount   *int64          `json:"valid_cell_count,omitempty"`
	MissingCellCount *int64          `json:"missing_cell_count,omitempty"`
	NoRainCellCount  *int64          `json:"no_rain_cell_count,omitempty"`
	CoverageRatio    *float64        `json:"coverage_ratio,omitempty"`
	Metadata         json.RawMessage `json:"-"`
}

type ProductManifestEntry struct {
	ProductID   uuid.UUID              `json:"product_id"`
	ProductType ProductType            `json:"product_type"`
	ValidTimes  []time.Time            `json:"valid_times"`
	MemberCount int                    `json:"member_count"`
	Assets      []ProductAssetManifest `json:"assets"`
}

type SourceForecastManifest struct {
	URI             string `json:"uri"`
	SHA256          string `json:"sha256"`
	ContractVersion string `json:"contract_version"`
}

type ApplicationProductManifest struct {
	ContractName         string                 `json:"contract_name"`
	ContractVersion      string                 `json:"contract_version"`
	RunID                uuid.UUID              `json:"run_id"`
	JobID                uuid.UUID              `json:"job_id"`
	ModelRunID           uuid.UUID              `json:"model_run_id"`
	IssueTime            time.Time              `json:"issue_time"`
	GridID               string                 `json:"grid_id"`
	GridConfigVersion    string                 `json:"grid_config_version"`
	CoordinateSHA256     string                 `json:"coordinate_sha256"`
	CoordinateBounds     []float64              `json:"coordinate_centre_bounds"`
	PixelEdgeBounds      []float64              `json:"pixel_edge_bounds"`
	Width                int                    `json:"width"`
	Height               int                    `json:"height"`
	LongitudeIntervalDeg float64                `json:"longitude_interval_deg"`
	LatitudeIntervalDeg  float64                `json:"latitude_interval_deg"`
	SourceForecast       SourceForecastManifest `json:"source_forecast"`
	ModelID              string                 `json:"model_id"`
	ModelVersion         string                 `json:"model_version"`
	ModelConfigVersion   string                 `json:"model_config_version"`
	ProductConfigVersion string                 `json:"product_config_version"`
	BuilderVersion       string                 `json:"builder_version"`
	RendererVersion      string                 `json:"renderer_version"`
	PaletteVersion       string                 `json:"palette_version"`
	Products             []ProductManifestEntry `json:"products"`
	CreatedAt            time.Time              `json:"created_at"`
}

type ProductBuildRecord struct {
	JobID                 uuid.UUID
	RunID                 uuid.UUID
	ModelRunID            uuid.UUID
	ForecastURI           string
	ForecastSHA256        string
	ProductConfigVersion  string
	BundleContractVersion string
	Status                string
	BundleURI             *string
	Manifest              *ApplicationProductManifest
	CreatedAt             time.Time
	UpdatedAt             time.Time
}
