# 7.3.3 source-constrained radial opening

Opt-in broad_source.radial_opening; old profiles remain frozen. Use scikit-image
opening on RAW valid DBZH >35 dBZ, physical radial length 50 km, one azimuth ray.
Ceil length to gates then choose odd footprint, preventing even-anchor shifts.
Outside array and missing gates are false; do not interpolate or bridge them.
Requires held-out BWS power relation, angular support and SNR bounds. Numeric
plateaus, explicit weather, local enhancement and geometry conflict remain vetoes.
Polar-envelope disagreement may become suspect quarantine, never confirmed RFI.
Reason bit 64 records morphology support; bit 16 retains polar disagreement when
morphology supplied the match. Existing baseline candidate behavior unchanged.
No blind mask deletion; keep raw and mark QPE/usable ineligible through the existing
quarantine path. Preserve experiment/audit mode and budget review flags.
