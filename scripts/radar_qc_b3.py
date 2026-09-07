#!/usr/bin/env python3
"""Build frozen radar QC labels, clutter priors, and promotion reports."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))

from rainpulse_algo.radar.qc_b3_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
