# S+X horizontal experiment

The explicitly enabled `experimental_horizontal_max` grid and each station's separate `experimental_enabled` switch admit an uncalibrated test product. The existing verified equal-height fusion gate is unchanged. Neither geometry nor calibration verification is invented.

The worker streams verified native cuts onto one projected horizontal grid and takes the maximum admitted reflectivity across cuts/stations. S reuses its registered QC values, frozen flag table, approved QC version and hard exclusion masks. X runs its existing polar QC and excludes confirmed/candidate contamination, low SNR, blockage, invalid values and exceeded attenuation limits. Unknown calibration/attenuation remains unverified. Unknown X quality scores remain missing. The product is not height aligned and has no trusted height interpretation. Missing and explicit no-echo remain distinct.

Sampling uses native ray elevation/range, WGS84 horizontal coordinates and the existing 4/3-Earth projection. The maximum angular nearest-ray tolerance is explicitly 1 degree. No dBZ averaging, PNG colour inversion, precipitation conversion or new forecast input is used. Source identity, input digest, winner ray/gate, observation age and contribution band are retained. Results carry `experimental=true`, `operational_eligible=false`, `qpe_eligible=false` and a visible uncalibrated experiment label.

Same-run S-only, X-only and S+X arrays feed the existing map, colour scale, source probes and contribution panels. This comparison S field uses the horizontal maximum method too; the previously published operational S product remains separate.

Validation: targeted Python experiments/multiband regression, Go planner/control-plane tests and frontend build. Real deployment acceptance must additionally verify actual S and X contribution counts and source-value probes.

Real-data corrections: exact duplicate bearings are resolved by latest acquisition (first original index on ties), preserving original ray indices. Both deployed X inputs omit SNR entirely. This experimental method retains such gates as uncertain candidates; a present SNR field that is low or invalid remains excluded. The existing X QC flags and trusted fusion eligibility are unchanged, X quality scores remain unknown, and the displayed warning explicitly identifies the missing SNR condition.

## Real acceptance

- Four S stations plus ZF101/ZF505, 2026-08-28 08:12 Beijing time: S 36,359 echo cells, X 9,496 and joint 37,087, with both bands selected as final winners. The source probe returns 25 dBZ at a verified X-winning sample, with low-quality state and original sweep/ray/gate indices; unknown height and quality remain null.
- Desktop and 375px mobile maps inspected. Reflectivity uses the shared segmented S palette; winner-band view shows distinct S/X contributions. Fixed a composite-specific CSS rule that had compressed the shared colour bar.
- The native catalog stores microsecond timestamps; floating-point ray conversion could differ by one float ULP (about 0.24 microseconds). Validation now compares at catalog microsecond precision, with a regression that still rejects a genuine 10-microsecond overrun.
- Two X sites each now have ten normalized volumes for UTC 00:00–01:00 through the existing history-ingest CLI. Earlier missing-input or failed runs remain traceable. New plans must be built after the updated Worker registers its fingerprint.
- Targeted Python experimental + multiband suite: 69 passed. Related Go operations/planner/control-plane/probe tests passed; frontend build and 5 focused workspace tests passed. Actual source-value API and rendered map checks supplement these tests.

### One-hour numeric acceptance

Run `126aaf50-043a-4acd-b276-509259e0773c`: all 10 six-minute products succeeded (08:06–09:00 Beijing time). All decoded joint arrays equal the missing-aware maximum of same-run S-only and X-only arrays; every X-winning echo retains the uncertainty mask. The first target has only causal X inputs; the other nine have six sources and both winning bands. X-winning echo counts across the nine joint targets are 3614, 2882, 5040, 4164, 4590, 5141, 5889, 5992 and 6449. This is real hourly acceptance, not a claim of full-day/all-station readiness.

Reproduce unit/regression checks from repository root:

```sh
PYTHONPATH=algorithms algorithms/.venv/bin/python -m pytest algorithms/tests/test_experimental_composite.py algorithms/tests/multiband -q
bash scripts/go_control.sh test ./internal/multiband ./internal/controlplane ./internal/operations ./internal/radarprobe
```
