# Fixed accumulation windows

Version: 1.0. Windows are relative to the UTC issue time, not clock hours.

| window_id | start_minute | end_minute | unit |
| --- | ---: | ---: | --- |
| hour_1 | 0 | 60 | mm |
| hour_2 | 60 | 120 | mm |
| total_2h | 0 | 120 | mm |

The numerical compute plane integrates the fixed five-minute rain-rate samples
at the **right endpoints** of each window: +5 through +60, +65 through +120,
and +5 through +120, respectively. Each sample represents its preceding
five-minute interval. This is a discrete product convention, not a claim of
continuous observed rainfall. T0 is not counted. Units convert by 5/60 hours.
Inputs must have exactly the stated cadence; no interpolation of missing times.

A cell is available only if every contributing sample is valid and finite.
Valid dry cells remain zero; missing cells remain NaN. Invalid first-hour cells
must not invalidate an otherwise complete second hour. Confidence is the minimum
of contributing samples. Low confidence is retained, not converted to missing.

Ensembles integrate each member separately before computing a mean or quantile.
The displayed ensemble statistic requires all members to have complete support.
P50(hour_1) + P50(hour_2) need not equal P50(total_2h).
Deterministic and individual member accumulations must be additive.

Observation/QPE windows use observations for the same issue-relative interval.
An incomplete observation sequence must not be filled with forecasts, zeros, or
the most recent available observation. Unavailable windows are labelled as such.

Display modes are `rain_rate`, `hourly`, and `total_2h`. Only forecast comparison
uses accumulation modes; QC and existing instantaneous verification stay unchanged.
The frontend never integrates arrays or images. Switching back to rain rate
restores the previously selected five-minute lead. Total mode has no playback.
