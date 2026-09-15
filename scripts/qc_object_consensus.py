#!/usr/bin/env python3
"""Local offline CLI; never connect to queues or publish a product."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'algorithms'))
from rainpulse_algo.radar.qc_engine.object_consensus.cli import main
if __name__ == '__main__':
    main()
