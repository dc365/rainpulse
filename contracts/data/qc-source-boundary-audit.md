# scikit-image source-boundary audit

Pinned scikit-image 0.26.0 RANSAC/LineModelND; outputs geometry evidence only.
Edges require two adjacent measured, geometry-valid rays at the same gate,
with one gate inside the object and one outside; gaps and missing neighbours
are not edges. Opposite edge orientations are fitted separately.
Coordinates use wradlib effective-Earth ground distance and radar-centred x/y.
Alternating 50 km range blocks train/validate; no forced origin constraint.
Report origin distance, train/holdout residual, span and coverage. A straight line
must also pass near the radar origin to count as radial geometry. Both boundaries
are required for a paired-sector hypothesis. No deletion or eligibility changes.

At each range, only the two external azimuth-envelope transitions are selected;
internal object holes cannot train the outside boundary. Circular angles are
unwrapped using the object's largest angular gap. Height must be explicit from
native metadata or a versioned station configuration; missing height abstains.
This effective-Earth geometric approximation does not establish cross-radar
height-datum compatibility or calibrated meteorological evidence.
