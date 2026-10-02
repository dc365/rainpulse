"""Nonrecursive global nominations from repeated original angular boundaries.

Original disconnected objects can share a measured radial corridor without
requiring a rejected-source anchor or a short maximum connectivity gap. Only
their exact original members are nominated; unknown intervening windows stay
unknown. Complete curved/forked/narrowing histories cannot restart as fragments.
"""

from dataclasses import replace

import numpy as np

from .geometry import ResourceLimit
from .object_model import RawObject

PARENT_COUNTEREXAMPLES = frozenset(
    (
        "ambiguous_fork_or_merge",
        "curved_or_drifting_centre",
        "narrowing_physical_width_weather_counterexample",
    )
)


def nominate(objects, beam, ranges, dr, *, maximum_trials=2000000):
    """Match every window to an immutable original seed, never pairwise grow."""
    trials = 0
    output = []
    identity = max((o.identity for o in objects), default=0)
    domains = sorted({(o.scale_m, o.level_dbz, o.native_segment_start) for o in objects})
    for scale, level, segment in domains:
        parents = [
            o
            for o in objects
            if o.scale_m == scale
            and o.level_dbz == level
            and o.native_segment_start == segment
            and not PARENT_COUNTEREXAMPLES.intersection(o.history_holds)
        ]
        windows = [w for o in parents for w in o.windows]
        origins = {id(w): o.identity for o in parents for w in o.windows}
        seeds = sorted({(w.left_deg, w.right_deg) for w in windows})
        seen = set()
        left = np.asarray([w.left_deg for w in windows])
        right = np.asarray([w.right_deg for w in windows])
        for seed_left, seed_right in seeds:
            trials += len(windows)
            if trials > maximum_trials:
                raise ResourceLimit(
                    "original boundary projection budget exceeded; no partial result"
                )
            indices = np.flatnonzero(
                (np.abs(left - seed_left) <= 0.5 * beam + 1e-6)
                & (np.abs(right - seed_right) <= 0.5 * beam + 1e-6)
            )
            by_block = {}
            for i in indices:
                window = windows[i]
                by_block.setdefault(window.block, []).append(window)
            # Competing original bands are an ambiguity, not an arbitrary
            # best match. Multiple identical nominations can be deduplicated.
            selected = []
            for block in sorted(by_block):
                choices = {w.members: w for w in by_block[block]}
                if len(choices) == 1:
                    selected.append(next(iter(choices.values())))
            original_ids = tuple(sorted({origins[id(w)] for w in selected}))
            if len(selected) < 5 or len(original_ids) < 2:
                continue
            members = tuple(sorted({i for w in selected for i in w.members}))
            if not members or members in seen:
                continue
            seen.add(members)
            columns = np.unique(np.asarray(members, dtype=np.int64) % len(ranges))
            start, end = float(ranges[columns[0]]), float(ranges[columns[-1]] + dr)
            if end-start < 150000 or len(columns)*dr < 20000:
                continue
            identity += 1
            # The full measured span includes all nominees. Empty or
            # unknown intervening coordinates are never added as members.
            clipped = tuple(replace(w, members=tuple(w.members)) for w in selected)
            output.append(
                RawObject(
                    identity=identity,
                    kind="projected",
                    scale_m=scale,
                    level_dbz=level,
                    start_m=start,
                    end_m=end,
                    support_m=float(len(columns) * dr),
                    windows=clipped,
                    history_holds=(),
                    origin_ids=original_ids,
                    native_segment_start=segment,
                )
            )
    return output
