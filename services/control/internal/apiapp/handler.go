package apiapp

import (
	"net/http"
	"os"

	"github.com/fonwee/rainpulse-nowcast/services/control/internal/alerting"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/api"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/buildinfo"
	ensembleproductstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/ensembleproducts"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/objectstore"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/orchestration"
	postgresstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/postgres"
	verificationstore "github.com/fonwee/rainpulse-nowcast/services/control/internal/verification"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workspace"
	"github.com/jackc/pgx/v5/pgxpool"
)

func NewHandler(pool *pgxpool.Pool) (http.Handler, error) {
	version := os.Getenv("RAINPULSE_VERSION")
	if version == "" {
		version = buildinfo.Identity()
	}
	store := postgresstore.New(pool)
	commands := orchestration.NewService(store, orchestration.Options{})
	diagnosticLayers, err := objectstore.NewFromEnvironment()
	if err != nil {
		return nil, err
	}
	verificationReports := verificationstore.NewFileStore(environmentOrDefault(
		"RAINPULSE_ALGORITHM_VERIFICATION_ROOT",
		"/var/lib/rainpulse/algorithm-verification",
	))
	ensembleRoot := environmentOrDefault(
		"RAINPULSE_ENSEMBLE_PRODUCT_ROOT",
		"/var/lib/rainpulse/ensemble-products",
	)
	nowcastNetRoot := environmentOrDefault(
		"RAINPULSE_NOWCASTNET_PRODUCT_ROOT",
		"/var/lib/rainpulse/nowcastnet-products",
	)
	ensembleProducts := ensembleproductstore.NewFileStore(ensembleRoot)
	alertReader, err := alerting.NewClient(
		environmentOrDefault("RAINPULSE_PROMETHEUS_URL", "http://prometheus:9090"),
		environmentOrDefault("RAINPULSE_ALERTMANAGER_URL", "http://alertmanager:9093"),
		alerting.Options{},
	)
	if err != nil {
		return nil, err
	}

	adminToken := os.Getenv("RAINPULSE_ADMIN_TOKEN")
	coreHandler := api.NewHandler(api.Options{
		Version:              version,
		AdminToken:           adminToken,
		Runs:                 store,
		Observations:         store,
		Commands:             commands,
		DiagnosticLayers:     diagnosticLayers,
		Products:             store,
		ProductObjects:       diagnosticLayers,
		Verification:         verificationReports,
		ForecastVerification: store,
		EnsembleProducts:     ensembleProducts,
		Metrics:              store,
		Alerts:               alertReader,
		OperationalIssues:    store,
	})
	return workspace.NewRuntimeHandler(coreHandler, workspace.RuntimeOptions{
		Store: store, ProjectionStore: store, Objects: diagnosticLayers, EnsembleRoot: ensembleRoot, NowcastNetRoot: nowcastNetRoot, AdminToken: adminToken,
	}), nil

}
