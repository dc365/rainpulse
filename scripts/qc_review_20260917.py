"""Run in the repository's pinned Python/worker environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms"))
from rainpulse_algo.radar.qc_engine.review_extension.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
