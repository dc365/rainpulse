package controlplane

import (
	"context"
	"fmt"
	"github.com/fonwee/rainpulse-nowcast/services/control/internal/workflow"
	"github.com/google/uuid"
)

func (p *pipelinePlanner) planQCBatch(ctx context.Context) error {
	b, e := p.store.ActiveQCBatch(ctx)
	if e != nil || b == nil {
		return e
	}
	active := map[string]int{}
	remaining, failures := 0, 0
	for _, i := range b.Items {
		switch i.Status {
		case "FAILED", "SKIPPED":
			failures++
		case "SUCCEEDED":
		default:
			remaining++
			if i.JobID != nil {
				active[i.Kind]++
			}
		}
	}
	if remaining == 0 {
		status := "SUCCEEDED"
		if failures > 0 {
			status = "FAILED"
		}
		return p.store.SetQCBatchStatus(ctx, b.ID, status)
	}
	if b.Status == "PENDING" {
		if e = p.store.SetQCBatchStatus(ctx, b.ID, "QC_RUNNING"); e != nil {
			return e
		}
	}
	for _, i := range b.Items {
		if i.JobID != nil || i.Status != "PENDING" || active[i.Kind] >= 4 {
			continue
		}
		var job workflow.Job
		if i.Kind == "qc" {
			_, job, e = createRadarQC(ctx, p.store, p.service, i.ID.String(), p.settings.qcConfig, b.ID)
		} else {
			ready, failed, err := p.store.QCBatchDisplayReady(ctx, b.ID, i.ID)
			if err != nil {
				return err
			}
			if !ready {
				continue
			}
			if failed {
				e = fmt.Errorf("所依赖的雷达质控失败，保留原对照图")
			} else {
				_, job, e = createAnalysisDiagnostics(ctx, p.store, p.service, i.ID.String(), p.settings.diagnosticConfig, b.ID)
			}
		}
		if err := p.store.RecordQCBatchJob(ctx, b.ID, i, job.ID, e); err != nil {
			return err
		}
		if e == nil && job.ID != uuid.Nil {
			active[i.Kind]++
		}
	}
	return nil
}
