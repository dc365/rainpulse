package operations

// Scripted database/sql transport tests, not a replacement for the PostgreSQL
// integration suite. They verify binding, projections and transaction boundaries.
import (
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

type sqlStep struct {
	Contains string
	Columns  []string
	Rows     [][]driver.Value
	Args     func([]driver.NamedValue)
	Err      error
	Exec     bool
}
type scriptDriver struct {
	t       *testing.T
	mu      sync.Mutex
	steps   []sqlStep
	commits int
}

func (d *scriptDriver) Open(string) (driver.Conn, error) { return &scriptConn{d}, nil }

type scriptConn struct{ d *scriptDriver }

func (*scriptConn) Prepare(string) (driver.Stmt, error) {
	return nil, errors.New("not a prepared test")
}
func (*scriptConn) Close() error                                                   { return nil }
func (c *scriptConn) Begin() (driver.Tx, error)                                    { return &scriptTx{c.d}, nil }
func (c *scriptConn) BeginTx(context.Context, driver.TxOptions) (driver.Tx, error) { return c.Begin() }
func (d *scriptDriver) step(query string, args []driver.NamedValue, exec bool) sqlStep {
	d.t.Helper()
	d.mu.Lock()
	defer d.mu.Unlock()
	if len(d.steps) == 0 {
		d.t.Fatalf("unexpected SQL: %s", query)
	}
	s := d.steps[0]
	d.steps = d.steps[1:]
	if s.Exec != exec || !strings.Contains(query, s.Contains) {
		d.t.Fatalf("SQL call mismatch: wanted %s exec=%v, got %s", s.Contains, s.Exec, query)
	}
	if s.Args != nil {
		s.Args(args)
	}
	return s
}
func (c *scriptConn) QueryContext(_ context.Context, q string, a []driver.NamedValue) (driver.Rows, error) {
	s := c.d.step(q, a, false)
	if s.Err != nil {
		return nil, s.Err
	}
	return &scriptRows{s.Columns, s.Rows}, nil
}
func (c *scriptConn) ExecContext(_ context.Context, q string, a []driver.NamedValue) (driver.Result, error) {
	s := c.d.step(q, a, true)
	return driver.RowsAffected(1), s.Err
}

type scriptTx struct{ d *scriptDriver }

func (t *scriptTx) Commit() error { t.d.commits++; return nil }
func (*scriptTx) Rollback() error { return nil }

type scriptRows struct {
	columns []string
	rows    [][]driver.Value
}

func (r *scriptRows) Columns() []string { return r.columns }
func (*scriptRows) Close() error        { return nil }
func (r *scriptRows) Next(v []driver.Value) error {
	if len(r.rows) == 0 {
		return io.EOF
	}
	copy(v, r.rows[0])
	r.rows = r.rows[1:]
	return nil
}
func scriptedStore(t *testing.T, steps ...sqlStep) (*Store, *scriptDriver) {
	t.Helper()
	d := &scriptDriver{t: t, steps: steps}
	name := "ops-script-" + NewID()
	sql.Register(name, d)
	db, e := sql.Open(name, "")
	if e != nil {
		t.Fatal(e)
	}
	db.SetMaxOpenConns(1)
	t.Cleanup(func() {
		db.Close()
		if len(d.steps) != 0 {
			t.Errorf("unused SQL steps: %d", len(d.steps))
		}
	})
	return &Store{DB: db}, d
}
func TestPoolPauseIsAuditedInOneTransaction(t *testing.T) {
	s, d := scriptedStore(t,
		sqlStep{Contains: "FOR UPDATE", Columns: []string{"revision", "mode"}, Rows: [][]driver.Value{{int64(1), "ACCEPTING"}}},
		sqlStep{Contains: "UPDATE ops_pool_controls", Exec: true, Args: func(a []driver.NamedValue) {
			if a[1].Value != "DRAINING" {
				t.Fatal(a)
			}
		}},
		sqlStep{Contains: "INSERT INTO ops_resource_events", Exec: true})
	if e := s.PoolAction(context.Background(), "qc", PoolAction{"drain", 1, "maintenance"}, "admin"); e != nil {
		t.Fatal(e)
	}
	if d.commits != 1 {
		t.Fatal("missing transaction commit")
	}
}
func TestPoolStaleRevisionDoesNotWrite(t *testing.T) {
	s, d := scriptedStore(t, sqlStep{Contains: "FOR UPDATE", Columns: []string{"revision", "mode"}, Rows: [][]driver.Value{{int64(3), "DRAINING"}}})
	e := s.PoolAction(context.Background(), "qc", PoolAction{"resume", 1, "stale browser"}, "admin")
	status, _, _ := errorStatus(e)
	if status != 409 || d.commits != 0 {
		t.Fatal(status, d.commits, e)
	}
}
func taskSteps(t *testing.T, id, run string) []sqlStep {
	spec := JSON(Spec{ID: id, Kind: "qc", Identity: identity("qc"), Request: json.RawMessage(`{"payload":{}}`)})
	now := time.Now().UTC()
	return []sqlStep{
		{Contains: "SELECT run_id::text", Columns: []string{"run_id"}, Rows: [][]driver.Value{{run}}},
		{Contains: "SELECT mode FROM ops_runs", Columns: []string{"mode"}, Rows: [][]driver.Value{{"ACTIVE"}}},
		{Contains: "FROM ops_tasks WHERE id=$1 FOR UPDATE", Columns: []string{"id", "run_id", "spec", "state", "generation", "attempt_no", "current_attempt", "error_code", "error_message", "created_at", "dispatched_at", "queued_at", "updated_at", "result"}, Rows: [][]driver.Value{{id, run, spec, "QUEUED", int64(1), int64(0), "", "", "", now, nil, now, now, []byte("null")}}},
	}
}
func TestDrainingPoolNeverAllocatesAnAttempt(t *testing.T) {
	id, run := NewID(), NewID()
	steps := append([]sqlStep{storageOpenStep()}, taskSteps(t, id, run)...)
	steps = append(steps, sqlStep{Contains: "FROM ops_pool_controls WHERE kind=$1 FOR SHARE", Columns: []string{"mode"}, Rows: [][]driver.Value{{"DRAINING"}}})
	s, d := scriptedStore(t, steps...)
	c, e := s.Claim(context.Background(), id, "worker-qc", 1)
	if e != nil || c.Decision != "paused" || d.commits != 0 {
		t.Fatal(c, e, d.commits)
	}
}
func TestBusyWorkerCannotClaimAnotherTask(t *testing.T) {
	id, run := NewID(), NewID()
	steps := append([]sqlStep{storageOpenStep()}, taskSteps(t, id, run)...)
	steps = append(steps,
		sqlStep{Contains: "FROM ops_pool_controls", Columns: []string{"mode"}, Rows: [][]driver.Value{{"ACCEPTING"}}},
		sqlStep{Contains: "ops_workers WHERE id=$1 FOR UPDATE", Columns: []string{"identity", "seen_at", "ready"}, Rows: [][]driver.Value{{JSON(identity("qc")), time.Now(), true}}},
		sqlStep{Contains: "FROM ops_attempts WHERE worker_id=$1", Columns: []string{"occupied"}, Rows: [][]driver.Value{{true}}})
	s, _ := scriptedStore(t, steps...)
	c, e := s.Claim(context.Background(), id, "worker-qc", 1)
	if e != nil || c.Decision != "busy" {
		t.Fatal(c, e)
	}
}
func TestJournalHTTPBackwardPagesAndBindsSearch(t *testing.T) {
	run := NewID()
	rows := [][]driver.Value{}
	for _, id := range []int64{9, 8, 7} {
		rows = append(rows, []driver.Value{id, run, "", "", int64(0), "error", "failure", "literal text", time.Now()})
	}
	s, _ := scriptedStore(t, sqlStep{Contains: "ORDER BY id DESC LIMIT $", Columns: []string{"id", "run_id", "task_id", "attempt_id", "sequence", "level", "event", "message", "at"}, Rows: rows, Args: func(a []driver.NamedValue) {
		got := []any{}
		for _, v := range a {
			got = append(got, v.Value)
		}
		want := []any{run, "error", "' OR true --", int64(3)}
		if !reflect.DeepEqual(got, want) {
			t.Fatal(got)
		}
	}})
	h := NewHandler(&Service{Store: s}, http.NotFoundHandler(), HTTPOptions{AdminToken: "secret"})
	req := httptest.NewRequest("GET", "/api/v1/admin/ops/runs/"+run+"/events?direction=backward&level=error&q=%27+OR+true+--&limit=2", nil)
	req.Header.Set("Authorization", "Bearer secret")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	var got struct {
		Items  []Event `json:"items"`
		Before int64   `json:"next_before"`
		After  int64   `json:"next_after"`
		More   bool    `json:"has_more"`
	}
	if e := json.Unmarshal(w.Body.Bytes(), &got); e != nil {
		t.Fatal(e)
	}
	if w.Code != 200 || len(got.Items) != 2 || got.Items[0].ID != 8 || got.Items[1].ID != 9 || got.Before != 8 || got.After != 9 || !got.More {
		t.Fatal(w.Code, w.Body.String())
	}
}
func TestDataStageIsBoundBeforeLimit(t *testing.T) {
	s, _ := scriptedStore(t, sqlStep{Contains: "OR ($4='qc_missing'", Columns: []string{"record"}, Rows: [][]driver.Value{}, Args: func(a []driver.NamedValue) {
		if a[3].Value != "qc_missing" || a[len(a)-1].Value != int64(51) {
			t.Fatal(a)
		}
	}})
	q, _ := ParseDataQuery(windowValues())
	q.Stage = "qc_missing"
	items, e := s.DataScans(context.Background(), q, 50, time.Time{}, "")
	if e != nil || items == nil {
		t.Fatal(items, e)
	}
}
func TestMarkerCheckUnavailableIsNotMislabelledMissing(t *testing.T) {
	id := NewID()
	scan := []byte(`{"normalized_uri":"s3://rainpulse/radar/volume"}`)
	s, _ := scriptedStore(t, sqlStep{Contains: "s.scan_id=$1", Columns: []string{"scan"}, Rows: [][]driver.Value{{scan}}}, sqlStep{Contains: "s.scan_id=$1", Columns: []string{"scan"}, Rows: [][]driver.Value{{scan}}}, sqlStep{Contains: "INSERT INTO ops_asset_checks", Exec: true})
	service := &Service{Store: s, Objects: unavailableObjects{}}
	result, e := service.CheckScanAsset(context.Background(), id, "normalized")
	if e != nil || result.State != "unverified" || result.Asset != nil {
		t.Fatal(result, e)
	}
}

type unavailableObjects struct{}

func (unavailableObjects) ReadObject(context.Context, string, int64) ([]byte, string, error) {
	return nil, "", errors.New("backend inaccessible")
}
