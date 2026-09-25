//go:build integration

package operations

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRadarWorkspaceCatalogIntegration(t *testing.T) {
	s := integrationStore(t)
	_, e := s.DB.Exec(`CREATE TABLE radars(radar_id text PRIMARY KEY,display_name text,current_config_version text);
 CREATE TABLE radar_config_versions(radar_id text,radar_config_version text,config jsonb);
 CREATE TABLE radar_scans(scan_id uuid,radar_id text,volume_start_time timestamptz,volume_end_time timestamptz);
 CREATE TABLE radar_scan_runs(scan_id uuid,run_id uuid,status text,normalized_uri text,qc_uri text,grid_uri text,radar_config_version text,created_at timestamptz,updated_at timestamptz);
 INSERT INTO radars VALUES('zf101','Test X','v1'),('zf505','X no scans','v1'),('s1','S station','v1');
 INSERT INTO radar_config_versions VALUES('zf101','v1','{"hardware":{"radar_band":"X"},"site":{"longitude_deg":119.3306,"latitude_deg":26.1758}}'),('zf505','v1','{"hardware":{"radar_band":"X"}}'),('s1','v1','{"hardware":{"radar_band":"S"}}');
 INSERT INTO radar_scans VALUES('12345678-1234-1234-1234-123456789abc','zf101','2026-08-28T00:00:00Z','2026-08-28T00:01:00Z'),('12345678-1234-1234-1234-123456789abd','zf101','2026-08-28T00:06:00Z','2026-08-28T00:07:00Z');
 INSERT INTO radar_scan_runs VALUES('12345678-1234-1234-1234-123456789abc','12345678-1234-1234-1234-123456789abe','NORMALIZED','s3://test/input',NULL,NULL,'v1',now(),now());`)
	if e != nil {
		t.Fatal(e)
	}
	plan, run, task, attempt := NewID(), NewID(), NewID(), NewID()
	_, e = s.DB.Exec(`INSERT INTO ops_plans(id,run_id,document,digest,expires_at,created_at) VALUES($1,$2,'{}','test',now(),now())`, plan, run)
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.DB.Exec(`INSERT INTO ops_runs(id,plan_id,name,actor,impact) VALUES($1,$2,'test','test','test')`, run, plan)
	if e != nil {
		t.Fatal(e)
	}
	spec := `{"request":{"payload":{"mode":"x_qc","radar_id":"zf101","scan_id":"12345678-1234-1234-1234-123456789abc"}},"identity":{"versions":{"network_release":"test-v1"}}}`
	_, e = s.DB.Exec(`INSERT INTO ops_tasks(id,run_id,ordinal,kind,spec,state,current_attempt,result) VALUES($1,$2,1,'multiband',$3,'SUCCEEDED',$4,'{"asset":{"uri":"s3://test/result"}}')`, task, run, spec, attempt)
	if e != nil {
		t.Fatal(e)
	}
	_, e = s.DB.Exec(`INSERT INTO ops_tasks(id,run_id,ordinal,kind,spec,state,error_message) VALUES($1,$2,2,'multiband',$3,'FAILED','new attempt failed')`, NewID(), run, spec)
	if e != nil {
		t.Fatal(e)
	}
	h := NewHandler(&Service{Store: s}, http.NotFoundHandler(), HTTPOptions{})
	get := func(path string) map[string]any {
		t.Helper()
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("GET", radarWorkspacePrefix+path, nil))
		if w.Code != 200 {
			t.Fatalf("%s %d %s", path, w.Code, w.Body.String())
		}
		var result map[string]any
		_ = json.Unmarshal(w.Body.Bytes(), &result)
		return result
	}
	stations := get("radar-stations?band=X")
	items := stations["items"].([]any)
	if len(items) != 2 || items[0].(map[string]any)["qc_ready"].(float64) != 1 || items[1].(map[string]any)["registered"].(float64) != 0 {
		t.Fatalf("%+v", stations)
	}
	if stations["start"] != "2026-08-27T16:00:00Z" {
		t.Fatalf("wrong local day: %v", stations["start"])
	}
	candidate := items[0].(map[string]any)["candidate_site"].(map[string]any)
	if items[0].(map[string]any)["geometry_status"] != "unverified" || candidate["longitude_deg"] != 119.3306 || candidate["latitude_deg"] != 26.1758 || candidate["coordinate_source"] != "draft_radar_config" || candidate["config_version"] != "v1" {
		t.Fatalf("draft map context changed qualification: %+v", items[0])
	}
	if items[1].(map[string]any)["candidate_site"] != nil {
		t.Fatalf("missing draft coordinates must not produce a site: %+v", items[1])
	}
	if _, e = s.DB.Exec(`UPDATE radar_config_versions SET config=jsonb_set(config,'{site,longitude_deg}','200') WHERE radar_id='zf101'`); e != nil {
		t.Fatal(e)
	}
	if get("radar-stations?band=X")["items"].([]any)[0].(map[string]any)["candidate_site"] != nil {
		t.Fatal("out-of-range draft coordinates must not be published")
	}
	scans := get("radar-scans?radar_id=zf101&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z")["items"].([]any)
	if len(scans) != 2 {
		t.Fatal(scans)
	}
	if scans[0].(map[string]any)["qc_status"] != "WAITING_DECODE" {
		t.Fatal(scans[0])
	}
	older := scans[1].(map[string]any)
	if older["state"] != "NORMALIZED" || older["qc_status"] != "READY" {
		t.Fatalf("successful candidate hidden: %+v", older)
	}
	if older["results"].([]any)[0].(map[string]any)["result_id"] != task+"."+attempt {
		t.Fatal(older)
	}
	if len(get("radar-scans?radar_id=s1&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z")["items"].([]any)) != 0 {
		t.Fatal("S entered X directory")
	}
	// A retired/unpublished candidate is not reported as usable merely because the task succeeded.
	if _, e = s.DB.Exec(`UPDATE ops_tasks SET result=NULL WHERE id=$1`, task); e != nil {
		t.Fatal(e)
	}
	// Remove the newer failed task so the unavailable success is the latest record.
	if _, e = s.DB.Exec(`DELETE FROM ops_tasks WHERE state='FAILED'`); e != nil {
		t.Fatal(e)
	}
	unavailable := get("radar-scans?radar_id=zf101&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z")["items"].([]any)[1].(map[string]any)
	if unavailable["qc_status"] != "ASSET_UNAVAILABLE" {
		t.Fatal(unavailable)
	}
	// Cursor pages retain the selected station and local-day filter.
	if _, e = s.DB.Exec(`INSERT INTO radar_scans SELECT md5(i::text)::uuid,'zf101',
 '2026-08-28T01:00:00Z'::timestamptz+i*interval '1 minute',
 '2026-08-28T01:01:00Z'::timestamptz+i*interval '1 minute' FROM generate_series(1,101) i`); e != nil {
		t.Fatal(e)
	}
	page := get("radar-scans?radar_id=zf101&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z")
	if len(page["items"].([]any)) != 100 || page["next_cursor"] == "" {
		t.Fatal(page)
	}
	next := get("radar-scans?radar_id=zf101&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z&cursor=" + page["next_cursor"].(string))
	if len(next["items"].([]any)) != 3 || next["next_cursor"] != "" {
		t.Fatal(next)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("GET", radarWorkspacePrefix+"radar-scans?radar_id=zf505&start=2026-08-27T16:00:00Z&end=2026-08-28T16:00:00Z&cursor="+page["next_cursor"].(string), nil))
	if w.Code != 422 {
		t.Fatal("cursor reused across stations", w.Code)
	}

}
