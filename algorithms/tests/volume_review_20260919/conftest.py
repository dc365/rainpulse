from pathlib import Path
import sys
# Load the actual delivered package without importing the optional Py-ART/Zarr stack.
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"rainpulse_algo/radar/qc_engine"))
