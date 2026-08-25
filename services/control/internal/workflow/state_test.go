package workflow

import "testing"

func TestRunStateTransitions(t *testing.T) {
	tests := []struct {
		from    RunStatus
		to      RunStatus
		allowed bool
	}{
		{RunWaiting, RunReceived, true},
		{RunReceived, RunValidating, true},
		{RunPreprocessing, RunInputReady, true},
		{RunInputReady, RunBaselineRunning, true},
		{RunBaselineRunning, RunBaselineReady, true},
		{RunPublished, RunVerifying, true},
		{RunVerifying, RunVerified, true},
		{RunBaselineReady, RunFailed, true},
		{RunVerified, RunWaiting, false},
		{RunFailed, RunReceived, false},
		{RunWaiting, RunPublished, false},
	}

	for _, test := range tests {
		if got := CanTransitionRun(test.from, test.to); got != test.allowed {
			t.Errorf("CanTransitionRun(%s, %s) = %t, want %t", test.from, test.to, got, test.allowed)
		}
	}
}

func TestJobStateTransitions(t *testing.T) {
	tests := []struct {
		from    JobStatus
		to      JobStatus
		allowed bool
	}{
		{JobPending, JobRunning, true},
		{JobPending, JobSucceeded, true},
		{JobRunning, JobSucceeded, true},
		{JobRunning, JobFailed, true},
		{JobSucceeded, JobRunning, false},
		{JobFailed, JobPending, false},
	}

	for _, test := range tests {
		if got := CanTransitionJob(test.from, test.to); got != test.allowed {
			t.Errorf("CanTransitionJob(%s, %s) = %t, want %t", test.from, test.to, got, test.allowed)
		}
	}
}
