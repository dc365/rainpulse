#!/usr/bin/env python3
"""Offline raw-episode build, contiguous-block holdout, and read-only audit."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.episode_background.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
