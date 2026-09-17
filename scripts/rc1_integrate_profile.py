"""Deterministic integration helper, executed and tested before packaging."""
from pathlib import Path
import re


def extend_versions(text):
    return re.sub(r'^( *)"qc-opensource-7\.3\.6",$',
                  r'\1"qc-opensource-7.3.6",\n\1"qc-opensource-7.4.0",', text, flags=re.M)


def apply(root):
    p = root / "algorithms/rainpulse_algo/radar/qc_engine/profile.py"
    s = p.read_text()
    if "radial_clutter: RadialClutterConfig" in s:
        return
    s = extend_versions(s)
    s = s.replace('"qc-opensource-7.3.6": "generalization-p0p2-v1",',
                  '"qc-opensource-7.3.6": "generalization-p0p2-v1",\n            "qc-opensource-7.4.0": "generalization-p0p2-v1",')
    s = s.replace('self.pipeline_version != "qc-opensource-7.3.6"',
                  'self.pipeline_version not in {"qc-opensource-7.3.6", "qc-opensource-7.4.0"}')
    s = s.replace('from .crossradar_profile import CrossRadarConfig',
                  'from .crossradar_profile import CrossRadarConfig\nfrom .radial_clutter.config import RadialClutterConfig')
    marker = '    residual_repair: ResidualRepairConfig | None = None'
    assert marker in s
    s = s.replace(marker, marker + '\n    radial_clutter: RadialClutterConfig | None = None')
    marker = '        data = json.dumps(value, sort_keys=True, separators=(",", ":"))'
    assert marker in s
    s = s.replace(marker, '        if self.radial_clutter is None:\n            value.pop("radial_clutter", None)\n' + marker)
    marker = '        if self.echo.dbzh_valid_range_dbz[0]'
    assert marker in s
    check = '''        if (self.pipeline_version == "qc-opensource-7.4.0") != (self.radial_clutter is not None):
            raise ValueError("RC1 requires its coordinated profile")
        if self.radial_clutter and self.radial_clutter.quarantine_quality >= self.quality_index.quantitative_minimum:
            raise ValueError("RC1 quarantine must be below quantitative eligibility")
'''
    s = s.replace(marker, check + marker)
    p.write_text(s)
    p = root / 'algorithms/rainpulse_algo/radar/qc_worker.py'
    p.write_text(extend_versions(p.read_text()))
    p = root / 'algorithms/rainpulse_algo/radar/qc_engine/broad_source.py'
    s = p.read_text().replace('    source_edge: bool = False', '    source_edge: bool = False\n    emit_self_reference: bool = False')
    s = s.replace('            if cfg.source_edge:', '            if cfg.source_edge or cfg.emit_self_reference:')
    s = s.replace('        "BWS_CANDIDATE_MASK": candidate.astype("uint8"),\n        "BWS_REASON": reason,', '        "BWS_CANDIDATE_MASK": candidate.astype("uint8"),\n        **({"BWS_SELF_REFERENCE_MASK": edge_reference.astype("uint8")} if cfg.emit_self_reference else {}),\n        "BWS_REASON": reason,')
    p.write_text(s)
