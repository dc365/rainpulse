package operations

import (
	"context"
	"encoding/json"
	"github.com/jackc/pgx/v5"
	"os"
	"strings"
	"testing"
	"time"
)

// Session-local tables keep this regression independent of operational data.
func compositeTimelineConnection(t *testing.T) (*pgx.Conn, context.Context) {
	t.Helper()
	dsn := os.Getenv("RAINPULSE_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("requires isolated PostgreSQL test database")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	t.Cleanup(cancel)
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	_, err = conn.Exec(ctx, `CREATE TEMP TABLE ops_tasks(id text,current_attempt text,spec jsonb,kind text,state text,result jsonb,updated_at timestamptz,run_id text);
 CREATE TEMP TABLE ops_retired_runs(run_id text);`)
	if err != nil {
		t.Fatal(err)
	}
	return conn, ctx
}

type compositeTimelinePage struct {
	Selected string `json:"selected_series_id"`
	Series   []struct {
		ID      string `json:"series_id"`
		Product string `json:"product_id"`
		Legacy  bool   `json:"legacy"`
	} `json:"series"`
	Items []struct {
		ID     string `json:"result_id"`
		At     string `json:"analysis_time"`
		Series string `json:"series_id"`
	} `json:"items"`
}

func compositeTimelinePageFor(t *testing.T, conn *pgx.Conn, ctx context.Context, series string) compositeTimelinePage {
	t.Helper()
	var raw []byte
	if err := conn.QueryRow(ctx, compositeTimelineSQL, "2026-08-28T00:00:00Z", "2026-08-29T00:00:00Z", series).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var page compositeTimelinePage
	if err := json.Unmarshal(raw, &page); err != nil {
		t.Fatal(err)
	}
	for _, item := range page.Items {
		if item.Series != page.Selected {
			t.Fatalf("mixed series: %+v", page)
		}
	}
	return page
}

func TestCompositeTimelineFullDayLatestAttempt(t *testing.T) {
	conn, ctx := compositeTimelineConnection(t)
	_, err := conn.Exec(ctx, `
 INSERT INTO ops_tasks SELECT i::text,'old',jsonb_build_object('request',jsonb_build_object('payload',jsonb_build_object('mode','sx_composite','analysis_time',('2026-08-28 00:00Z'::timestamptz+i*interval '6 minutes')::text))),'multiband','SUCCEEDED','{"asset":{"uri":"test"}}','2026-09-01','active' FROM generate_series(0,239) i;
 INSERT INTO ops_tasks SELECT id,'new',spec,kind,state,result,'2026-09-02',run_id FROM ops_tasks;
 INSERT INTO ops_tasks SELECT id,'retired',spec,kind,state,result,'2026-09-03','retired' FROM ops_tasks WHERE current_attempt='new';
 INSERT INTO ops_retired_runs VALUES ('retired');`)
	if err != nil {
		t.Fatal(err)
	}
	page := compositeTimelinePageFor(t, conn, ctx, "")
	if len(page.Series) != 1 || !page.Series[0].Legacy {
		t.Fatal("legacy same-run series changed", page)
	}
	seen := map[string]bool{}
	for _, item := range page.Items {
		id, at := item.ID, item.At
		if seen[at] || len(id) < 4 || id[len(id)-4:] != ".new" {
			t.Fatalf("wrong version/duplicate: %s %s", id, at)
		}
		seen[at] = true
	}
	if len(page.Items) != 240 {
		t.Fatalf("got %d frames, want 240", len(page.Items))
	}
}

func TestCompositeTimelineSeparatesFrozenSeries(t *testing.T) {
	conn, ctx := compositeTimelineConnection(t)
	insert := func(id, product, fingerprint, code, network, execution, run, state, at, updated string, published bool) {
		t.Helper()
		spec := JSON(map[string]any{"identity": map[string]any{"fingerprint": fingerprint, "code_sha256": code},
			"request": map[string]any{"payload": map[string]any{"mode": "sx_composite", "product_id": product,
				"analysis_time": at, "network_sha256": network, "execution_sha256": execution, "requested_radars": []string{"s1", "x1"},
				"s_qc_policy_complete": true, "s_qc_policy": map[string]any{"s1": map[string]any{"pipeline_version": "stored", "parameters_sha256": strings.Repeat("b", 64), "implementation_revision": "python-source-tree-v1:" + strings.Repeat("c", 64), "profile": "actual", "libraries": map[string]string{"wradlib": "v"}}}}}})
		var result any
		if published {
			result = `{"asset":{"uri":"test"}}`
		}
		_, err := conn.Exec(ctx, `INSERT INTO ops_tasks VALUES($1,'attempt',$2::jsonb,'multiband',$3,$4::jsonb,$5,$6)`, id, string(spec), state, result, updated, run)
		if err != nil {
			t.Fatal(err)
		}
	}
	fp := strings.Repeat("a", 64)
	// Same processing series spans runs. A horizontal frame in its gap must not fill it.
	insert("v2-first", "v2", fp, "code1", "net1", "exec1", "r1", "SUCCEEDED", "2026-08-28T00:06:00Z", "2026-09-01T00:00:00Z", true)
	insert("v2-last", "v2", fp, "code1", "net1", "exec1", "r2", "SUCCEEDED", "2026-08-28T00:18:00Z", "2026-09-02T00:00:00Z", true)
	insert("horizontal", "horizontal", fp, "code1", "net1", "exec1", "r1", "SUCCEEDED", "2026-08-28T00:12:00Z", "2026-09-01T01:00:00Z", true)
	insert("changed-code", "v2", fp, "code2", "net1", "exec1", "r1", "SUCCEEDED", "2026-08-28T00:06:00Z", "2026-09-01T01:00:00Z", true)
	insert("changed-network", "v2", fp, "code1", "net2", "exec1", "r1", "SUCCEEDED", "2026-08-28T00:06:00Z", "2026-09-01T01:00:00Z", true)
	insert("changed-execution", "v2", fp, "code1", "net1", "exec2", "r1", "SUCCEEDED", "2026-08-28T00:06:00Z", "2026-09-01T01:00:00Z", true)
	insert("changed-radars", "v2", fp, "code1", "net1", "exec1", "r1", "SUCCEEDED", "2026-08-28T00:12:00Z", "2026-09-01T01:00:00Z", true)
	if _, err := conn.Exec(ctx, `UPDATE ops_tasks SET spec=jsonb_set(spec,'{request,payload,requested_radars}','["s1","x2"]') WHERE id='changed-radars';
 UPDATE ops_tasks SET spec=jsonb_set(spec,'{request,payload,requested_radars}','["x1","s1"]') WHERE id='v2-last'`); err != nil {
		t.Fatal(err)
	}
	insert("legacy1", "v2", "", "", "", "", "legacy1", "SUCCEEDED", "2026-08-28T00:06:00Z", "2026-09-01T01:00:00Z", true)
	insert("legacy2", "v2", "", "", "", "", "legacy2", "SUCCEEDED", "2026-08-28T00:12:00Z", "2026-09-01T01:00:00Z", true)
	if _, err := conn.Exec(ctx, `UPDATE ops_tasks SET spec=jsonb_set(spec,'{request,payload,requested_radars}','null') WHERE id='legacy1'`); err != nil {
		t.Fatal(err)
	}
	for _, x := range []struct {
		id, state, at, run string
		published          bool
	}{
		{"failed", "FAILED", "2026-08-28T00:12:00Z", "r1", true},
		{"unpublished", "SUCCEEDED", "2026-08-28T00:12:00Z", "r1", false},
		{"retired", "SUCCEEDED", "2026-08-28T00:12:00Z", "retired", true},
		{"end-exclusive", "SUCCEEDED", "2026-08-29T00:00:00Z", "r1", true},
	} {
		insert(x.id, "v2", fp, "code1", "net1", "exec1", x.run, x.state, x.at, "2026-09-03T00:00:00Z", x.published)
	}
	if _, err := conn.Exec(ctx, `INSERT INTO ops_retired_runs VALUES('retired')`); err != nil {
		t.Fatal(err)
	}
	page := compositeTimelinePageFor(t, conn, ctx, "")
	if len(page.Series) != 8 || len(page.Items) != 2 || page.Items[0].ID != "v2-last.attempt" || page.Items[1].ID != "v2-first.attempt" {
		t.Fatalf("mixed or lost frozen series: %+v", page)
	}
	selected := compositeTimelinePageFor(t, conn, ctx, page.Selected)
	if len(selected.Items) != 2 {
		t.Fatal(selected)
	}
	for _, series := range page.Series {
		if series.Product == "horizontal" {
			h := compositeTimelinePageFor(t, conn, ctx, series.ID)
			if len(h.Items) != 1 || h.Items[0].ID != "horizontal.attempt" {
				t.Fatal(h)
			}
		}
	}
	unknown := compositeTimelinePageFor(t, conn, ctx, strings.Repeat("f", 64))
	if len(unknown.Items) != 0 || unknown.Selected != strings.Repeat("f", 64) {
		t.Fatal("silently fell back", unknown)
	}
}

func TestCompositeTimelineSeparatesActualUpstreamQCPolicies(t *testing.T) {
	conn, ctx := compositeTimelineConnection(t)
	insert := func(id, run, at string, recipe map[string]any, complete bool) {
		t.Helper()
		spec := JSON(map[string]any{"identity": map[string]any{"fingerprint": strings.Repeat("a", 64)}, "request": map[string]any{"payload": map[string]any{"mode": "sx_composite", "product_id": "v2", "requested_radars": []string{"s1", "x1"}, "analysis_time": at, "s_qc_policy": map[string]any{"s1": recipe}, "s_qc_policy_complete": complete}}})
		if _, err := conn.Exec(ctx, `INSERT INTO ops_tasks VALUES($1,'attempt',$2::jsonb,'multiband','SUCCEEDED','{"asset":{"uri":"test"}}','2026-09-01',$3)`, id, string(spec), run); err != nil {
			t.Fatal(err)
		}
	}
	base := map[string]any{"pipeline_version": "stored", "parameters_sha256": strings.Repeat("b", 64), "implementation_revision": "python-source-tree-v1:" + strings.Repeat("c", 64), "profile": "actual", "libraries": map[string]string{"wradlib": "v"}}
	insert("first", "r1", "2026-08-28T00:06:00Z", base, true)
	insert("missing-station", "r2", "2026-08-28T00:12:00Z", base, true)
	insert("last", "r3", "2026-08-28T00:18:00Z", base, true)
	for key, value := range map[string]any{"parameters_sha256": strings.Repeat("d", 64), "implementation_revision": "python-source-tree-v1:" + strings.Repeat("e", 64), "libraries": map[string]string{"wradlib": "changed"}} {
		copy := map[string]any{}
		for k, v := range base {
			copy[k] = v
		}
		copy[key] = value
		insert(key, "changed", "2026-08-28T00:12:00Z", copy, true)
	}
	insert("unknown-r1", "unknown1", "2026-08-28T00:12:00Z", base, false)
	insert("unknown-r2", "unknown2", "2026-08-28T00:18:00Z", base, false)
	page := compositeTimelinePageFor(t, conn, ctx, "")
	if len(page.Series) != 6 {
		t.Fatalf("upstream QC variants or unknown runs merged: %+v", page)
	}
	found := false
	for _, series := range page.Series {
		frames := compositeTimelinePageFor(t, conn, ctx, series.ID)
		for _, frame := range frames.Items {
			if frame.ID == "first.attempt" {
				found = true
				if series.Legacy || len(frames.Items) != 3 {
					t.Fatal("missing station changed expected recipe or cross-runs were lost", frames)
				}
			}
		}
	}
	if !found {
		t.Fatal("configured series disappeared")
	}
}
