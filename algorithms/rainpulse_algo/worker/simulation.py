from __future__ import annotations

import json

from .contracts import JobRequested


class SimulatedFailure(RuntimeError):
    pass


def execute(request: JobRequested) -> tuple[bytes, dict[str, float]]:
    if request.payload.parameters.get("force_failure") is True:
        raise SimulatedFailure("RP-005 simulated worker failure")

    result = json.dumps(
        {
            "schema_version": "1.0",
            "simulation": True,
            "run_id": str(request.run_id),
            "job_id": str(request.job_id),
            "issue_time": request.payload.issue_time.isoformat(),
            "lead_minutes": list(range(5, 125, 5)),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return result, {"simulation": 1.0, "lead_count": 24.0}
