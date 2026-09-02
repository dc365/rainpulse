package main

import (
	"testing"
	"time"
)

func TestMosaicWaterlinePublishesEarlyWhenAllRadarsArrive(t *testing.T) {
	analysis := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	decision := decideMosaicWaterline(
		analysis.Add(30*time.Second), analysis, 4, 4, 30*time.Second, 4*time.Minute, false,
	)
	if !decision.Ready || !decision.Complete || decision.Reason != "all_radars_arrived" {
		t.Fatalf("unexpected complete decision: %#v", decision)
	}
}

func TestMosaicWaterlineWaitsForLateRadarsThenDegrades(t *testing.T) {
	analysis := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	waiting := decideMosaicWaterline(
		analysis.Add(3*time.Minute), analysis, 2, 4, 30*time.Second, 4*time.Minute, false,
	)
	if waiting.Ready || waiting.Reason != "waiting_maximum_watermark" {
		t.Fatalf("unexpected waiting decision: %#v", waiting)
	}
	ready := decideMosaicWaterline(
		analysis.Add(4*time.Minute), analysis, 2, 4, 30*time.Second, 4*time.Minute, false,
	)
	if !ready.Ready || ready.Complete || ready.Reason != "maximum_wait_elapsed" {
		t.Fatalf("unexpected degraded decision: %#v", ready)
	}
}

func TestMosaicWaterlineNeverDegradesWhenAllRadarsAreRequired(t *testing.T) {
	analysis := time.Date(2026, 9, 1, 0, 0, 0, 0, time.UTC)
	decision := decideMosaicWaterline(
		analysis.Add(20*time.Minute), analysis, 3, 4, 30*time.Second, 4*time.Minute, true,
	)
	if decision.Ready || decision.Reason != "waiting_all_radars" {
		t.Fatalf("unexpected require-all decision: %#v", decision)
	}
}

func TestRealtimeShadowAcceptsLegacyNonOperationalInputTransport(t *testing.T) {
	if !pipelineModeCompatible("realtime_shadow", "historical_replay") {
		t.Fatal("realtime shadow must accept the fail-closed NowcastInput 1.2 transport mode")
	}
	if pipelineModeCompatible("operational", "historical_replay") {
		t.Fatal("operational mode must never accept non-operational replay input")
	}
}
