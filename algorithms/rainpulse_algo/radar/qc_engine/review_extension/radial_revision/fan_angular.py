"""Cross-ray original-source interpolation, independently checked; no actions."""
import numpy as np
from ..arrays import mask, moment, native_geometry
from .fan_states import states
from .raw_fans import PREFIX as FAN
from .source_ledger import PREFIX as LEDGER


def diagnose(native, blocked, group, *, target_mask=None):
    r, az, dr, good, gaps = native_geometry(native)
    z, observed = moment(native, 'DBZH')
    snr, snr_ok = moment(native, 'SNR')
    snr = np.where(snr_ok, snr, np.nan)
    blocked = mask(blocked, native.shape, 'angular barriers') | ~good[:, None]
    family = np.asarray(group[FAN+'ID'])
    seed = np.asarray(group[LEDGER+'SEED_ID'])
    candidates = (family > 0) & (seed == 0) & observed & ~blocked
    if target_mask is not None:
        candidates &= mask(target_mask, native.shape, 'angular targets')
    shape = native.shape
    out = {k:np.zeros(shape, 'uint8') for k in ('CANDIDATE_MASK','MODEL_AVAILABLE_MASK','MATCH_MASK')}
    out.update({k:np.zeros(shape, 'uint32') for k in ('LEFT_SOURCE_ID','RIGHT_SOURCE_ID')})
    out.update({k:np.full(shape, np.nan, 'float32') for k in
                ('INTERCEPT_DB','RESIDUAL_DB','VALIDATION_P90_DB','REFERENCE_RAYS')})
    out['CANDIDATE_MASK'][:] = candidates
    records = []
    decisions = {k:0 for k in ('fewer_than_three_original_rays','fewer_than_three_nearby_rays',
        'fewer_than_three_guarded_models','not_bracketed','interior_reference_disagrees',
        'beyond_original_distance','model_available')}
    block_ids = (r//20000).astype(int)
    # A row-group and gap identity prohibits wrap-around or crossing missing rays.
    sections = np.r_[0, np.cumsum(gaps[:-1])]
    cache = {}
    for parent in np.unique(family[candidates]):
        rows, cols = np.where(candidates & (family == parent))
        original_rows = np.flatnonzero(((family == parent) & (seed > 0)).any(axis=1))
        if len(original_rows) < 3:
            decisions['fewer_than_three_original_rays'] += len(cols)
            continue
        for target_row in np.unique(rows):
            targets = cols[rows == target_row]
            beam = float(np.nanmedian(np.asarray(group[FAN+'BEAM_DEG'])[target_row,targets]))
            delta = (az[original_rows]-az[target_row]+180.)%360.-180.
            nearby = original_rows[(abs(delta) <= 2*beam+1e-6) &
                                   (sections[original_rows] == sections[target_row]) & (original_rows != target_row)]
            if len(nearby) < 3:
                decisions['fewer_than_three_nearby_rays'] += len(targets)
                continue
            for block in np.unique(block_ids[targets]):
                use = targets[block_ids[targets] == block]
                reference = []
                for row in nearby:
                    cache_key = (int(parent), int(row), int(block))
                    if cache_key not in cache:
                        models = []
                        sources = np.unique(seed[row,(family[row] == parent) & (seed[row] > 0)])
                        for source in sources:
                            original = np.flatnonzero(seed[row] == source)
                            # Full original source identity, but no reference across
                            # a protected radial path between source and target block.
                            interval = (r >= min(r[original].min(),r[use].min())) & (r <= max(r[original].max(),r[use].max()))
                            if blocked[row,interval].any():
                                continue
                            fitted = states(r,dr,z[row],snr[row],original,block)
                            if len(fitted) == 1:
                                models.append((int(source), fitted[0][1], original))
                        cache[cache_key] = models[0] if len(models) == 1 else None
                    model = cache[cache_key]
                    if model is None:
                        continue
                    between = slice(min(row,target_row),max(row,target_row)+1)
                    if blocked[between][:,block_ids == block].any():
                        continue
                    # All reference gates must also have a safe angular path;
                    # original source references cannot hop across weather.
                    if blocked[between][:,model[2]].any():
                        continue
                    angle = float((az[row]-az[target_row]+180.)%360.-180.)
                    reference.append((angle,int(row),*model))
                reference.sort(key=lambda x:x[0])
                if len(reference)<3:
                    decisions['fewer_than_three_guarded_models'] += len(use)
                    continue
                if reference[0][0]>=0 or reference[-1][0]<=0:
                    decisions['not_bracketed'] += len(use)
                    continue
                left,right = reference[0],reference[-1]
                slope = (right[3]['INTERCEPT_DB']-left[3]['INTERCEPT_DB'])/(right[0]-left[0])
                intercept = left[3]['INTERCEPT_DB']-slope*left[0]
                error = [abs(x[3]['INTERCEPT_DB']-(intercept+slope*x[0])) for x in reference[1:-1]]
                p90 = float(np.percentile(error,90))
                if max(error)>2.5:
                    decisions['interior_reference_disagrees'] += len(use)
                    continue
                nearest = [np.min(abs(r[use,None]-r[x[4]][None,:]),axis=1) for x in (left,right)]
                accepted = use[(nearest[0]<=120000.) & (nearest[1]<=120000.)]
                decisions['beyond_original_distance'] += len(use)-len(accepted)
                decisions['model_available'] += len(accepted)
                if not len(accepted):
                    continue
                residual = z[target_row,accepted]-20*np.log10(np.maximum(r[accepted],1.)/50000.)-intercept
                out['MODEL_AVAILABLE_MASK'][target_row,accepted] = 1
                out['LEFT_SOURCE_ID'][target_row,accepted] = left[2]
                out['RIGHT_SOURCE_ID'][target_row,accepted] = right[2]
                for key,value in (('INTERCEPT_DB',intercept),('RESIDUAL_DB',residual),
                                  ('VALIDATION_P90_DB',p90),('REFERENCE_RAYS',len(reference))):
                    out[key][target_row,accepted] = value
                out['MATCH_MASK'][target_row,accepted] = abs(residual)<=2.5
                records.append({'parent_id':int(parent),'target_row':int(target_row),'target_block':int(block),
                    'source_rows':[x[1] for x in reference], 'source_ids':[x[2] for x in reference],
                    'validation_p90_db':p90,'slope_db_per_degree':float(slope),'target_gates':accepted.tolist()})
    if sum(decisions.values()) != int(candidates.sum()):
        raise ValueError('angular probe decision partition incomplete')
    return out, {'version':'fan-angular-probe-v1','candidate_gates':int(candidates.sum()),
                 'model_available_gates':int(out['MODEL_AVAILABLE_MASK'].sum()),
                 'matched_gates':int(out['MATCH_MASK'].sum()),'actions':0,'filled_gates':0,
                 'source_claim':False,'recursive_growth':False,'decision_counts':decisions,'records':records}
