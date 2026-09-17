"""Small checked integration edits; old selected profiles retain old behavior."""
from rc1_integrate_profile import extend_versions


def apply(root):
    folder = root / 'algorithms/rainpulse_algo/radar/qc_engine'
    p = folder / 'runner.py'
    s = p.read_text()
    if 'RC1 context/core evidence' in s:
        return
    s = extend_versions(s)
    marker = '        optional = {**evidence.arrays, **decision.arrays, **phase}'
    assert marker in s
    block = '''        # RC1 context/core evidence uses a separately validated final-stage ledger.
        rc1_record = None
        if profile.radial_clutter is not None:
            from .radial_clutter.disposition import apply as apply_rc1
            from .radial_clutter.resources import prior_for_native
            rc1_started = perf_counter()
            decision, quality, observed, low, flags, rc1_record = apply_rc1(
                sweep, decision, quality, flags, profile.radial_clutter,
                profile.flag_masks["LOW_QUALITY"],
                prior=prior_for_native(ancillary_maps or {}, sweep), weather_support=weather,
            )
            timings[sweep.name + ".rc1_ms"] = (perf_counter() - rc1_started) * 1000
'''
    s = s.replace(marker, block + marker)
    s = s.replace('        sweep_records[sweep.name] = {', '        sweep_records[sweep.name] = {\n            **({"radial_clutter": rc1_record} if rc1_record is not None else {}),')
    p.write_text(s)
    p = folder / 'context.py'
    s = p.read_text()
    marker = '    identity = {\n        "parameters_hash": profile.parameters_hash,'
    assert marker in s
    block = '''    rc1_prior_record = None
    if profile.radial_clutter is not None:
        from .radial_clutter.resources import prepare_priors
        from ..qc_worker import _load_npz
        ancillary, rc1_prior_record = prepare_priors(
            profile, lambda uri: _load_npz(uri, client), view.root, ancillary
        )
'''
    s = s.replace(marker, block + marker)
    marker = '    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()'
    s = s.replace(marker, '    if rc1_prior_record is not None:\n        identity["rc1_prior"] = rc1_prior_record\n' + marker)
    p.write_text(s)
    p = folder / 'validation.py'
    s = p.read_text().replace('def validate_sweep(group, attrs) -> None:', 'def _validate_legacy_sweep(group, attrs) -> None:', 1)
    s += '''\n\ndef validate_sweep(group, attrs) -> None:
    if attrs.get("qc_pipeline_version") == "qc-opensource-7.4.0":
        from .radial_clutter.validation import validate
        validate(group, attrs, _validate_legacy_sweep)
    else:
        _validate_legacy_sweep(group, attrs)
'''
    p.write_text(s)
