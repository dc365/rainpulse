from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(root/'rainpulse_algo/radar/qc_engine'),str(root/'tests/clutter_fusion_20260921'),str(root/'tests/volume_review_20260919')]
