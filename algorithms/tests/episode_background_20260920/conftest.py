from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'algorithms/rainpulse_algo/radar/qc_engine'))
sys.path.insert(0, str(Path(__file__).parent))
