package planning

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"
)

func testScope() Scope {
	start := time.Date(2026, 9, 22, 6, 0, 0, 0, time.UTC)
	return Scope{Windows: []Window{{start, start.Add(time.Hour)}}, RadarIDs: []string{"Z9598", " z9591 ", "z9598"}, GridID: "fuzhou", Status: "READY"}
}

func TestFiltersBeforeLimitAndStableCursor(t *testing.T) {
	for _, kind := range []string{"radar", "analysis", "forecast"} {
		t.Run(kind, func(t *testing.T) {
			scope := testScope()
			scope.ExcludeQCOnly = true
			cursor := &Cursor{scope.Windows[0].Start, "00000000-0000-0000-0000-000000000001"}
			sql, args, err := Predicate(kind, scope, cursor, 200)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(sql, "OFFSET") || !strings.Contains(sql, "::uuid)") {
				t.Fatalf("not stable keyset: %s", sql)
			}
			if strings.Index(sql, "WHERE") > strings.Index(sql, "LIMIT") {
				t.Fatal(sql)
			}
			if args[len(args)-1] != 200 {
				t.Fatal(args)
			}
			if kind == "radar" && (!strings.Contains(sql, "qc_batch_items") || !strings.Contains(sql, "ANY")) {
				t.Fatal(sql)
			}
			if kind == "analysis" && !strings.Contains(sql, "regeneration_request_id IS NOT NULL") {
				t.Fatal(sql)
			}
		})
	}
}

func TestValuesAreBoundNotSQL(t *testing.T) {
	s := testScope()
	s.GridID = "'; DROP TABLE jobs;--"
	s.Status = "' OR true --"
	sql, args, err := Predicate("analysis", s, nil, 20)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(sql, s.GridID) || strings.Contains(sql, s.Status) {
		t.Fatal(sql)
	}
	if args[0] != s.Status {
		t.Fatal(args)
	}
}

func TestRejectUnboundedQueries(t *testing.T) {
	cases := []func(*Scope){
		func(s *Scope) { s.Windows = nil }, func(s *Scope) { s.Windows[0].Start = time.Time{} },
		func(s *Scope) { s.Windows[0].End = s.Windows[0].Start }, func(s *Scope) { s.Status = "" },
		func(s *Scope) { s.RadarIDs = nil }, func(s *Scope) { s.IncludeReruns = true },
	}
	for i, f := range cases {
		s := testScope()
		f(&s)
		if _, _, err := Predicate("radar", s, nil, 200); err == nil {
			t.Fatalf("case %d accepted", i)
		}
	}
	for _, n := range []int{0, -1, 201} {
		if _, _, err := Predicate("radar", testScope(), nil, n); err == nil {
			t.Fatal(n)
		}
	}
}

func TestMergeAndKeepDisjointReplay(t *testing.T) {
	s := testScope()
	a := s.Windows[0]
	b := Window{a.Start.Add(-24 * time.Hour), a.Start.Add(-23 * time.Hour)}
	merged, err := NormalizeWindows([]Window{a, b, {a.Start.Add(30 * time.Minute), a.End.Add(time.Hour)}})
	if err != nil || len(merged) != 2 || merged[1].End != a.End.Add(time.Hour) {
		t.Fatalf("%v %v", merged, err)
	}
}

func TestRadarIDsNormalized(t *testing.T) {
	_, args, err := Predicate("radar", testScope(), nil, 200)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(args[3], []string{"z9591", "z9598"}) {
		t.Fatal(args)
	}
}

func TestRerunExceptionAndVerification(t *testing.T) {
	s := testScope()
	s.IncludeReruns = true
	sql, _, err := Predicate("forecast", s, nil, 200)
	if err != nil || !strings.Contains(sql, "OR rerun_of IS NOT NULL") {
		t.Fatal(sql, err)
	}
	s.IncludeReruns = false
	sql, _, _ = Predicate("forecast", s, nil, 200)
	if strings.Contains(sql, "rerun_of") {
		t.Fatal(sql)
	}
}

func TestInvalidCursorRejected(t *testing.T) {
	for _, c := range []*Cursor{{Time: time.Now(), ID: "x'"}, {ID: "00000000-0000-0000-0000-000000000001"}} {
		if _, _, err := Predicate("forecast", testScope(), c, 200); err == nil {
			t.Fatal(c)
		}
	}
}

func TestBoundedCache(t *testing.T) {
	now := time.Now()
	m := map[int]time.Time{0: now.Add(-time.Hour)}
	for i := 1; i <= 250; i++ {
		m[i] = now.Add(time.Duration(i) * time.Second)
	}
	Prune(m, now, time.Minute, 200)
	if len(m) != 200 {
		t.Fatal(len(m))
	}
	if _, ok := m[0]; ok {
		t.Fatal("expired item retained")
	}
	for i := 1; i <= 50; i++ {
		if _, ok := m[i]; ok {
			t.Fatalf("old item %d retained", i)
		}
	}
}

func TestMoreThan200SameTimestampCursorOrder(t *testing.T) {
	// Pure ordering regression; real SQL execution is covered by the optional
	// postgres test shipped alongside the adapter, not by this simulation.
	rows := make([]Cursor, 451)
	at := testScope().Windows[0].Start
	for i := range rows {
		rows[i] = Cursor{at, fmt.Sprintf("00000000-0000-0000-0000-%012d", i+1)}
	}
	seen := map[string]bool{}
	var cursor *Cursor
	for {
		page := []Cursor{}
		for _, r := range rows {
			if cursor == nil || r.Time.After(cursor.Time) || r.Time.Equal(cursor.Time) && r.ID > cursor.ID {
				page = append(page, r)
				if len(page) == 200 {
					break
				}
			}
		}
		for _, r := range page {
			if seen[r.ID] {
				t.Fatal("duplicate")
			}
			seen[r.ID] = true
		}
		if len(page) < 200 {
			break
		}
		c := page[len(page)-1]
		cursor = &c
	}
	if len(seen) != 451 {
		t.Fatal(len(seen))
	}
}
