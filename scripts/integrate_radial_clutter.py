"""Run once on the pinned review checkout before tests and ZIP creation."""
from pathlib import Path
import json
import yaml

import rc1_integrate_profile
import rc1_integrate_runner

ROOT = Path(__file__).resolve().parents[1]
rc1_integrate_profile.apply(ROOT)
rc1_integrate_runner.apply(ROOT)
folder = ROOT / 'algorithms/rainpulse_algo/radar/qc_engine'
p = folder / 'profile.py'
s = p.read_text()
if '                "emit_self_reference",' not in s:
    s = s.replace('                "source_edge",', '                "source_edge",\n                "emit_self_reference",')
p.write_text(s)
p = folder / 'radial_clutter/config.py'
s = p.read_text()
if 'class PriorAsset' not in s:
    s = s.replace('class RadialClutterConfig(BaseModel):', '''class PriorAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    partition_id: str = Field(min_length=1)


class RadialClutterConfig(BaseModel):''')
    s = s.replace('    radial_enabled: bool = True', '    priors: dict[str, PriorAsset] = Field(default_factory=dict)\n    radial_enabled: bool = True')
    s = s.replace('        if max(self.shoulder_offsets_deg) >= 90:', '        if len(self.radial_lengths_m) > 8:\n            raise ValueError("at most eight morphology scales")\n        if max(self.shoulder_offsets_deg) >= 90:')
p.write_text(s)
p = folder / 'radial_clutter/rules.py'
s = p.read_text().replace(' & f["reliable"]\n', ' & measured(n, "SNR")\n    stationary &= n.fields.get("SNR", np.full(n.shape, np.nan)) >= cfg.minimum_reliable_snr_db\n')
p.write_text(s)
p = folder / 'radial_clutter/prior_asset.py'
s = p.read_text().replace('"partition_id": builder.partition,', '"partition_id": builder.partition, "cut_name": builder.reference.name,')
s = s.replace('    result["receipt_sha256"] = expected_sha', '    result["cut_name"] = meta["cut_name"]\n    result["receipt_sha256"] = expected_sha')
p.write_text(s)
base = yaml.safe_load((ROOT / 'configs/qc/fujian-qc-near-sector-v1.yaml').read_text())
base['pipeline_version'] = 'qc-opensource-7.4.0'
base['generalization']['broad_source']['emit_self_reference'] = True
for mode in ('audit', 'experiment_quarantine'):
    name = 'fujian-qc-radial-clutter-' + ('audit' if mode == 'audit' else 'experiment') + '-v1'
    base['profile_version'] = name
    base['radial_clutter'] = {'mode': mode, 'acknowledge_uncalibrated': mode != 'audit', 'priors': {}}
    (ROOT / 'configs/qc' / (name + '.yaml')).write_text(yaml.safe_dump(base, sort_keys=False))
# Prior configurations and parameters remain byte-frozen. Old hash tests exclude
# a new absent optional extension, just as previous absent extensions do.
for p in (ROOT / 'algorithms/tests').glob('test_*.py'):
    s = p.read_text()
    marker = '    data.pop("generalization", None)'
    if marker in s and 'data.pop("radial_clutter", None)' not in s:
        s = s.replace(marker, marker + '\n    data.pop("radial_clutter", None)')
        p.write_text(s)
print(json.dumps({'integration': 'generated', 'pipeline': 'qc-opensource-7.4.0', 'production_selected': False}))
