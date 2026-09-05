package workspace

import (
	"context"
	"fmt"
	"sort"
	"time"

	nowcastnetproducts "github.com/fonwee/rainpulse-nowcast/services/control/internal/nowcastnetproducts"
)

type nowcastNetProductStore interface {
	ListCycles(context.Context) ([]nowcastnetproducts.Bundle, error)
	ReadAsset(context.Context, string, string) (nowcastnetproducts.AssetContent, error)
	ReadObject(context.Context, string, string) ([]byte, string, error)
}

type formalNowcastNetProductStore struct {
	runs    NowcastNetAlgorithmRunStore
	objects RuntimeObjectReader
}

func newFormalNowcastNetProductStore(
	runs NowcastNetAlgorithmRunStore,
	objects RuntimeObjectReader,
) nowcastNetProductStore {
	if runs == nil || objects == nil {
		return nil
	}
	return formalNowcastNetProductStore{runs: runs, objects: objects}
}

func (store formalNowcastNetProductStore) ListCycles(ctx context.Context) ([]nowcastnetproducts.Bundle, error) {
	runs, err := store.runs.ListCompletedNowcastNetAlgorithmRuns(ctx, 500)
	if err != nil {
		return nil, err
	}
	bundles := make([]nowcastnetproducts.Bundle, 0, len(runs))
	for _, run := range runs {
		bundle, err := store.readBundle(ctx, run)
		if err != nil {
			return nil, err
		}
		bundles = append(bundles, bundle)
	}
	sort.Slice(bundles, func(left, right int) bool {
		return bundles[left].CreatedAt.After(bundles[right].CreatedAt)
	})
	return bundles, nil
}

func (store formalNowcastNetProductStore) ReadAsset(
	ctx context.Context,
	bundleID string,
	assetID string,
) (nowcastnetproducts.AssetContent, error) {
	runs, err := store.runs.ListCompletedNowcastNetAlgorithmRuns(ctx, 500)
	if err != nil {
		return nowcastnetproducts.AssetContent{}, err
	}
	for _, run := range runs {
		bundle, err := store.readBundle(ctx, run)
		if err != nil {
			return nowcastnetproducts.AssetContent{}, err
		}
		if bundle.BundleID.String() != bundleID {
			continue
		}
		for _, frame := range bundle.Frames {
			if frame.AssetID != assetID {
				continue
			}
			data, digest, err := store.objects.Read(ctx, run.OutputURI, frame.ObjectPath)
			if err != nil {
				return nowcastnetproducts.AssetContent{}, err
			}
			if digest != frame.SHA256 || int64(len(data)) != frame.SizeBytes {
				return nowcastnetproducts.AssetContent{}, fmt.Errorf("formal NowcastNet asset integrity differs")
			}
			return nowcastnetproducts.AssetContent{Data: data, MediaType: frame.MediaType, SHA256: digest}, nil
		}
		return nowcastnetproducts.AssetContent{}, nowcastnetproducts.ErrNotFound
	}
	return nowcastnetproducts.AssetContent{}, nowcastnetproducts.ErrNotFound
}

func (store formalNowcastNetProductStore) ReadObject(
	ctx context.Context,
	bundleID string,
	objectPath string,
) ([]byte, string, error) {
	runs, err := store.runs.ListCompletedNowcastNetAlgorithmRuns(ctx, 500)
	if err != nil {
		return nil, "", err
	}
	for _, run := range runs {
		bundle, err := store.readBundle(ctx, run)
		if err != nil {
			return nil, "", err
		}
		if bundle.BundleID == run.JobID && bundle.BundleID.String() == bundleID {
			return store.objects.Read(ctx, run.OutputURI, objectPath)
		}
	}
	return nil, "", nowcastnetproducts.ErrNotFound
}

func (store formalNowcastNetProductStore) readBundle(
	ctx context.Context,
	run NowcastNetAlgorithmRun,
) (nowcastnetproducts.Bundle, error) {
	data, _, err := store.objects.Read(ctx, run.OutputURI, "manifest.json")
	if err != nil {
		return nowcastnetproducts.Bundle{}, fmt.Errorf("read formal NowcastNet manifest: %w", err)
	}
	bundle, err := nowcastnetproducts.DecodeBundle(data)
	if err != nil {
		return nowcastnetproducts.Bundle{}, err
	}
	if bundle.BundleID != run.JobID || bundle.RunID != run.RunID || bundle.AlgorithmRunID != run.AlgorithmRunID ||
		!bundle.IssueTime.Equal(run.IssueTime) || bundle.GridID != run.GridID {
		return nowcastnetproducts.Bundle{}, fmt.Errorf("formal NowcastNet manifest provenance differs")
	}
	return bundle, nil
}

type combinedNowcastNetProductStore struct {
	formal nowcastNetProductStore
	legacy nowcastNetProductStore
}

func newCombinedNowcastNetProductStore(
	formal nowcastNetProductStore,
	legacy nowcastNetProductStore,
) nowcastNetProductStore {
	if formal == nil {
		return legacy
	}
	if legacy == nil {
		return formal
	}
	return combinedNowcastNetProductStore{formal: formal, legacy: legacy}
}

func (store combinedNowcastNetProductStore) ListCycles(ctx context.Context) ([]nowcastnetproducts.Bundle, error) {
	formal, err := store.formal.ListCycles(ctx)
	if err != nil {
		return nil, err
	}
	legacy, err := store.legacy.ListCycles(ctx)
	if err != nil && err != nowcastnetproducts.ErrNotFound {
		return nil, err
	}
	byCycle := newestNowcastNetBundlesByCycle(legacy)
	// Formal NATS products are authoritative over legacy file products, while
	// repeated formal runs at the same valid time must select the newest bundle.
	for key, bundle := range newestNowcastNetBundlesByCycle(formal) {
		byCycle[key] = bundle
	}
	values := make([]nowcastnetproducts.Bundle, 0, len(byCycle))
	for _, bundle := range byCycle {
		values = append(values, bundle)
	}
	sort.Slice(values, func(left, right int) bool { return values[left].CreatedAt.After(values[right].CreatedAt) })
	return values, nil
}

func newestNowcastNetBundlesByCycle(
	bundles []nowcastnetproducts.Bundle,
) map[string]nowcastnetproducts.Bundle {
	values := make(map[string]nowcastnetproducts.Bundle, len(bundles))
	for _, bundle := range bundles {
		key := bundle.GridID + "/" + bundle.IssueTime.UTC().Format(time.RFC3339)
		current, exists := values[key]
		if !exists || bundle.CreatedAt.After(current.CreatedAt) {
			values[key] = bundle
		}
	}
	return values
}

func (store combinedNowcastNetProductStore) ReadAsset(ctx context.Context, bundleID string, assetID string) (nowcastnetproducts.AssetContent, error) {
	asset, err := store.formal.ReadAsset(ctx, bundleID, assetID)
	if err == nil || err != nowcastnetproducts.ErrNotFound {
		return asset, err
	}
	return store.legacy.ReadAsset(ctx, bundleID, assetID)
}

func (store combinedNowcastNetProductStore) ReadObject(ctx context.Context, bundleID string, objectPath string) ([]byte, string, error) {
	data, digest, err := store.formal.ReadObject(ctx, bundleID, objectPath)
	if err == nil || err != nowcastnetproducts.ErrNotFound {
		return data, digest, err
	}
	return store.legacy.ReadObject(ctx, bundleID, objectPath)
}
