# QC workspace shared analysis clock

Single station, multiple station and composite views preserve the same selected six-minute UTC analysis time and calendar day on switching modes. Native acquisition times do not replace that clock. Display acquisition start/end separately.

For a published composite with recorded S sources, native views use its exact station/scan identity and actual QC content hash. Resolve an immutable diagnostic RAW/QC pair for that scan, sweep and QC content; never substitute a nominally nearer or future volume, a different rebuild, or the next analysis image. A source must end at or before analysis time and be at most 720 seconds old. Missing or conflicting lineage yields an explicit missing view. Old products without source identity may remain explicitly labelled native reference views, not assertions of source alignment.

Lookup is bounded to neighbouring cycle diagnostics; it reads only metadata, not radar arrays, and does not calculate new composites. Preserve series identity across all three views. Each image label uses analysis time with the recorded acquisition range in the explanation. Async responses must retain the request identity and must not attach prior-time images to a new clock.
