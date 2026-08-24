package workflow

type RunStatus string

const (
	RunWaiting         RunStatus = "WAITING"
	RunReceived        RunStatus = "RECEIVED"
	RunValidating      RunStatus = "VALIDATING"
	RunPreprocessing   RunStatus = "PREPROCESSING"
	RunBaselineRunning RunStatus = "BASELINE_RUNNING"
	RunBaselineReady   RunStatus = "BASELINE_READY"
	RunEnhancedRunning RunStatus = "ENHANCED_RUNNING"
	RunProductBuilding RunStatus = "PRODUCT_BUILDING"
	RunPublished       RunStatus = "PUBLISHED"
	RunVerifying       RunStatus = "VERIFYING"
	RunVerified        RunStatus = "VERIFIED"
	RunDegraded        RunStatus = "DEGRADED"
	RunFailed          RunStatus = "FAILED"
	RunSkipped         RunStatus = "SKIPPED"
)

type JobStatus string

const (
	JobPending   JobStatus = "PENDING"
	JobRunning   JobStatus = "RUNNING"
	JobSucceeded JobStatus = "SUCCEEDED"
	JobFailed    JobStatus = "FAILED"
	JobSkipped   JobStatus = "SKIPPED"
)

var runTransitions = map[RunStatus]map[RunStatus]struct{}{
	RunWaiting:         {RunReceived: {}},
	RunReceived:        {RunValidating: {}},
	RunValidating:      {RunPreprocessing: {}},
	RunPreprocessing:   {RunBaselineRunning: {}},
	RunBaselineRunning: {RunBaselineReady: {}},
	RunBaselineReady: {
		RunEnhancedRunning: {},
		RunProductBuilding: {},
	},
	RunEnhancedRunning: {RunProductBuilding: {}},
	RunProductBuilding: {RunPublished: {}},
	RunPublished:       {RunVerifying: {}},
	RunVerifying:       {RunVerified: {}},
}

func CanTransitionRun(from, to RunStatus) bool {
	if from == to {
		return true
	}
	if isTerminalRun(from) {
		return false
	}
	if to == RunDegraded || to == RunFailed || to == RunSkipped {
		return true
	}
	_, allowed := runTransitions[from][to]
	return allowed
}

func CanTransitionJob(from, to JobStatus) bool {
	if from == to {
		return true
	}
	switch from {
	case JobPending:
		return to == JobRunning || to == JobSucceeded || to == JobFailed || to == JobSkipped
	case JobRunning:
		return to == JobSucceeded || to == JobFailed || to == JobSkipped
	default:
		return false
	}
}

func isTerminalRun(status RunStatus) bool {
	return status == RunVerified || status == RunDegraded || status == RunFailed || status == RunSkipped
}
