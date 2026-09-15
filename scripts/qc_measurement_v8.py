#!/usr/bin/env python3
"""Entry point usable from a checkout; no production service integration."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))
from rainpulse_algo.radar.qc_engine.measurement_v8.cli import main

if __name__ == "__main__":
    main()
