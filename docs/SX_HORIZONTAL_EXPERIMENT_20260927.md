# S+X horizontal experiment

The explicitly enabled `experimental_horizontal_max` grid and each station's separate `experimental_enabled` switch admit an uncalibrated test product. The existing verified equal-height fusion gate is unchanged. Neither geometry nor calibration verification is invented.

The worker streams verified native cuts onto one projected horizontal grid and takes the maximum admitted reflectivity across cuts/stations. S reuses its registered QC values, frozen flag table, approved QC version and hard exclusion masks. X runs its existing polar QC and excludes confirmed/candidate contamination, low SNR, blockage, invalid values and exceeded attenuation limits. Unknown calibration/attenuation remains unverified. Unknown X quality scores remain missing. The product is not height aligned and has no trusted height interpretation. Missing and explicit no-echo remain distinct.

Sampling uses native ray elevation/range, WGS84 horizontal coordinates and the existing 4/3-Earth projection. The maximum angular nearest-ray tolerance is explicitly 1 degree. No dBZ averaging, PNG colour inversion, precipitation conversion or new forecast input is used. Source identity, input digest, winner ray/gate, observation age and contribution band are retained. Results carry `experimental=true`, `operational_eligible=false`, `qpe_eligible=false` and a visible uncalibrated experiment label.

Same-run S-only, X-only and S+X arrays feed the existing map, colour scale, source probes and contribution panels. This comparison S field uses the horizontal maximum method too; the previously published operational S product remains separate.

Validation: targeted Python experiments/multiband regression, Go planner/control-plane tests and frontend build. Real deployment acceptance must additionally verify actual S and X contribution counts and source-value probes.

Real-data corrections: exact duplicate bearings are resolved by latest acquisition (first original index on ties), preserving original ray indices. Both deployed X inputs omit SNR entirely. This experimental method retains such gates as uncertain candidates; a present SNR field that is low or invalid remains excluded. The existing X QC flags and trusted fusion eligibility are unchanged, X quality scores remain unknown, and the displayed warning explicitly identifies the missing SNR condition.
