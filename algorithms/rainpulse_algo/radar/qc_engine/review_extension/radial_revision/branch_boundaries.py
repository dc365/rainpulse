"""Frozen RAW boundary hypotheses inside complete split/merge histories.

These measurements do not authorize QC. A seed is an original angular run,
never a residual or a newly associated member. Every hypothesis is evaluated
against every window of its complete parent; failed windows cannot disappear.
"""

import numpy as np

from .morphology_objects import _shoulders


def measure(
    entries,
    rows,
    left_edges,
    right_edges,
    beam,
    ranges,
    dr,
    z,
    observed,
    snr,
    snr_valid,
    barred,
    weather,
    charge,
    lower_labels,
    lower_boxes,
    lower_cache,
    enclosed_branches=False,
    variable_boundaries=False,
):
    seeds = sorted({(e["a"], e["b"]) for e in entries})
    blocks = sorted({e["block"] for e in entries})
    by_block = {block: [] for block in blocks}
    for e in entries:
        by_block[e["block"]].append(e)
    original_left = np.array([e["left"] for e in entries])
    original_right = np.array([e["right"] for e in entries])
    all_columns = np.unique(np.concatenate([e["anchor"] for e in entries]))
    full_start = float(ranges[all_columns[0]])
    full_end = float(ranges[all_columns[-1]] + dr)
    hypotheses = []
    for a, b in seeds:
        width = float(right_edges[b - 1] - left_edges[a])
        if width > 90:
            continue
        charge(len(entries))
        compatible = (
            (np.abs(original_left - left_edges[a]) <= beam * 0.5 + 1e-6)
            & (np.abs(original_right - right_edges[b - 1]) <= beam * 0.5 + 1e-6)
        )
        if variable_boundaries:
            # Every boundary remains tied to the ORIGINAL seed. No accepted
            # member becomes a new anchor, so small steps cannot drift forever.
            # Centre tolerance remains strict while each exterior may breathe
            # within two measured beam widths. All memberships are RAW runs.
            seed_centre = (left_edges[a] + right_edges[b - 1]) / 2
            compatible = (
                (np.abs((original_left + original_right) / 2 - seed_centre) <= beam * 0.5 + 1e-6)
                & (np.abs(original_left - left_edges[a]) <= beam * 2 + 1e-6)
                & (np.abs(original_right - right_edges[b - 1]) <= beam * 2 + 1e-6)
            )
        compatible_indices = np.flatnonzero(compatible)
        enclosed_by_block = {block: [] for block in blocks}
        crossing_blocks = set()
        if enclosed_branches:
            for e in entries:
                inside = (
                    e["left"] >= left_edges[a] - beam * 0.5 - 1e-6
                    and e["right"] <= right_edges[b - 1] + beam * 0.5 + 1e-6
                )
                if inside:
                    enclosed_by_block[e["block"]].append(e)
                elif max(e["left"], left_edges[a]) < min(e["right"], right_edges[b - 1]):
                    crossing_blocks.add(e["block"])
        possible_enclosures = set()
        for block, members in enclosed_by_block.items():
            if len(members) < 2 or block in crossing_blocks:
                continue
            if (
                abs(min(e["left"] for e in members) - left_edges[a]) <= beam * 0.5 + 1e-6
                and abs(max(e["right"] for e in members) - right_edges[b - 1]) <= beam * 0.5 + 1e-6
            ):
                possible_enclosures.add(block)
        # Repeated boundaries are necessary before visiting full corridors.
        # This is independent of targets and does not change any qualification.
        if len({entries[i]["block"] for i in compatible_indices} | possible_enclosures) < 6:
            continue
        compatible_by_block = {block: [] for block in blocks}
        for i in compatible_indices:
            e = entries[i]
            compatible_by_block[e["block"]].append(e)
        matched = []
        history = []
        # Match only against the immutable seed; no pairwise chaining.
        for block in blocks:
            members = by_block[block]
            compatible = compatible_by_block[block]
            state = "ambiguous" if len(compatible) > 1 else "unmatched"
            if len(compatible) == 1:
                e = compatible[0]
                matched.append(e)
                state = "matched"
            elif not compatible and block in possible_enclosures:
                enclosed = enclosed_by_block[block]
                first = min(e["a"] for e in enclosed)
                last = max(e["b"] for e in enclosed)
                columns = np.unique(np.concatenate([e["anchor"] for e in enclosed]))
                charge((last - first) * len(columns))
                corridor = np.ix_(rows[first:last], columns)
                # Actual SNR is a receiver observation even when nonquiet.
                # This proves coverage only, never an absence of weather or
                # an exterior contrast. Missing DBZH is never filled.
                known_paths = ((observed[corridor] | snr_valid[corridor]) & ~barred[corridor]).all(
                    axis=0
                )
                if known_paths.mean() >= 0.8:
                    e = dict(
                        a=first,
                        b=last,
                        block=block,
                        anchor=columns,
                        left=min(e["left"] for e in enclosed),
                        right=max(e["right"] for e in enclosed),
                        rr=np.concatenate([e["rr"] for e in enclosed]),
                        cc=np.concatenate([e["cc"] for e in enclosed]),
                        original_children=[
                            (int(rows[e["a"]]), int(rows[e["b"] - 1]) + 1) for e in enclosed
                        ],
                        nonquiet_snr_without_dbzh=int(
                            (~observed[corridor] & snr_valid[corridor] & (snr[corridor] > 3)).sum()
                        ),
                    )
                    matched.append(e)
                    state = "enclosed_measured_branches"
                else:
                    state = "unobserved_enclosed_path"
            history.append(
                dict(
                    block=int(block),
                    state=state,
                    original_boundaries=[(float(e["left"]), float(e["right"])) for e in members],
                )
            )
        support = (
            np.unique(np.concatenate([e["anchor"] for e in matched]))
            if matched
            else np.array([], int)
        )
        holds = []
        if len(matched) < 6 or len(support) * dr < 20000 or full_end - full_start < 100000:
            holds.append("insufficient_boundary_support")
        if len(matched) / len(blocks) < 0.8:
            holds.append("unstable_complete_boundary_history")
        maximum_edge_step = 0.0
        if variable_boundaries and len(matched) > 1:
            ordered = sorted(matched, key=lambda e: e['block'])
            maximum_edge_step = max(
                max(abs(after['left'] - before['left']), abs(after['right'] - before['right']))
                / beam
                for before, after in zip(ordered, ordered[1:])
            )
            # Gaps do not multiply the allowed step. Missing windows cannot
            # authorize a larger jump to another original object.
            if maximum_edge_step > 2.0 + 1e-6:
                holds.append('unstable_variable_boundary_step')
        # Keep observations along the ENTIRE fixed branch corridor, including
        # merged/failed windows. A clean far tail cannot restart after weather.
        corridor_a, corridor_b = a, b
        if variable_boundaries and matched:
            corridor_a = min(a, min(e['a'] for e in matched))
            corridor_b = max(b, max(e['b'] for e in matched))
        charge((corridor_b - corridor_a) * len(all_columns))
        ix = np.ix_(rows[corridor_a:corridor_b], all_columns)
        if barred[ix].any():
            holds.append("original_branch_barrier")
        if weather[ix].any():
            holds.append("original_branch_weather")
        known = []
        clear = []
        for e in matched:
            charge((e["b"] - e["a"] + 2) * len(e["anchor"]))
            k, c = _shoulders(
                rows, e["a"], e["b"], e["anchor"], z, observed, snr, snr_valid, barred
            )
            known.append(k)
            clear.append(c)
        if not known or np.mean(known) < 0.8:
            holds.append("unknown_boundary_shoulders")
        if not clear or np.mean(clear) < 0.8:
            holds.append("insufficient_boundary_contrast")
        # Narrowing across all original members remains a competing weather
        # explanation, even if only its far endpoints match this seed.
        # A split changes individual child widths, not the complete parent's
        # outer envelope. Measuring children separately would mislabel a
        # fixed-angle fork as a narrowing weather ribbon.
        parent_widths = np.array(
            [
                max(e["right"] for e in by_block[block]) - min(e["left"] for e in by_block[block])
                for block in blocks
            ]
        )
        parent_radii = np.array(
            [
                ranges[np.unique(np.concatenate([e["anchor"] for e in by_block[block]]))].mean()
                for block in blocks
            ]
        )
        if (
            len(blocks) >= 4
            and parent_radii.max() / parent_radii.min() >= 1.7
            and np.ptp(parent_widths) > beam
        ):
            slope = float(np.polyfit(np.log(parent_radii), np.log(parent_widths), 1)[0])
            corr = float(np.corrcoef(np.log(parent_radii), np.log(parent_widths))[0, 1])
            if slope <= -0.4 and corr <= -0.7:
                holds.append("complete_parent_narrowing_weather")
        rr = []
        cc = []
        for e in matched:
            rr.extend(e["rr"].tolist())
            cc.extend(e["cc"].tolist())
        rr = np.asarray(rr, int)
        cc = np.asarray(cc, int)
        parent_ids = np.unique(lower_labels[np.searchsorted(rows, rr), cc])
        parent_ids = parent_ids[parent_ids > 0]
        if not len(parent_ids):
            holds.append("no_complete_lower_parent")
        for identity in parent_ids:
            identity = int(identity)
            if identity not in lower_cache:
                box = lower_boxes[identity - 1]
                charge(np.prod(lower_labels[box].shape))
                lr, lc = np.where(lower_labels[box] == identity)
                lr += box[0].start
                lc += box[1].start
                parent_holds = []
                if weather[rows[lr], lc].any():
                    parent_holds.append("complete_lower_parent_weather")
                if barred[rows[lr], lc].any():
                    parent_holds.append("complete_lower_parent_barrier")
                windows = (ranges[lc] // 10000).astype(int)
                widths = []
                radii = []
                for block in np.unique(windows):
                    selected = windows == block
                    widths.append(right_edges[lr[selected]].max() - left_edges[lr[selected]].min())
                    radii.append(ranges[lc[selected]].mean())
                widths = np.asarray(widths)
                radii = np.asarray(radii)
                if len(widths) >= 4 and radii.max() / radii.min() >= 1.7 and np.ptp(widths) > beam:
                    slope = np.polyfit(np.log(radii), np.log(widths), 1)[0]
                    corr = np.corrcoef(np.log(radii), np.log(widths))[0, 1]
                    if slope <= -0.4 and corr <= -0.7:
                        parent_holds.append("complete_lower_parent_narrowing_weather")
                lower_cache[identity] = parent_holds
            holds.extend(lower_cache[identity])
        hypotheses.append(
            (
                rr,
                cc,
                dict(
                    seed_left_deg=float(left_edges[a]),
                    seed_right_deg=float(right_edges[b - 1]),
                    original_start_m=full_start,
                    original_end_m=full_end,
                    original_windows=len(blocks),
                    matched_windows=len(matched),
                    matched_support_m=float(len(support) * dr),
                    history=history,
                    matched_original_runs=[
                        dict(
                            block=int(e["block"]),
                            native_row_start=child[0],
                            native_row_end=child[1],
                        )
                        for e in matched
                        for child in e.get(
                            "original_children", [(int(rows[e["a"]]), int(rows[e["b"] - 1]) + 1)]
                        )
                    ],
                    enclosed_measured_windows=sum(
                        w["state"] == "enclosed_measured_branches" for w in history
                    ),
                    enclosed_nonquiet_snr_without_dbzh=sum(
                        e.get("nonquiet_snr_without_dbzh", 0) for e in matched
                    ),
                    internal_snr_is_not_dry_evidence=True,
                    original_lower_parent_ids=[int(i) for i in parent_ids],
                    known_shoulders_fraction=float(np.mean(known)) if known else 0.0,
                    clear_shoulders_fraction=float(np.mean(clear)) if clear else 0.0,
                    geometry_qualified=not holds,
                    holds=sorted(set(holds)),
                    production_eligible=False,
                    recursive_growth=False,
                    variable_boundary_mode=bool(variable_boundaries),
                    maximum_seed_edge_offset_beams=2.0 if variable_boundaries else 0.5,
                    maximum_seed_centre_offset_beams=0.5,
                    measured_maximum_edge_step_beams=float(maximum_edge_step),
                ),
            )
        )
    return hypotheses
