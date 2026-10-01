#!/usr/bin/env python3
"""Replay RAW fragment nominations from saved --diagnostics NPZ; no server writes."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import types
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--joint',action='store_true',help='Also qualify frozen families using saved original independent sources and local polar measurements')
    parser.add_argument('--ledger',action='store_true',help='Freeze complete original source tracks and bounded geometric links; no additional actions')
    parser.add_argument('--window-source',action='store_true',help='Replay windowed original-source qualification (includes complete ledger); no product writes')
    parser.add_argument('--raw-fans',action='store_true',help='Freeze broad RAW families with original ledger clues; diagnostic only')
    parser.add_argument('--fan-joint',action='store_true',help='Check guarded original-source models on broad families; no product writes')
    parser.add_argument('--power-states',action='store_true',help='Diagnose guarded original power states; implies fan joint, never adds actions')
    parser.add_argument('--angular-probe',action='store_true',help='Diagnose independently checked original-source angular interpolation; never adds actions')
    parser.add_argument('--past-sources',type=Path,help='Jointly qualify bounded past sources from the frozen read-only audit receipt; implies RAW fans, no product writes')
    parser.add_argument('--source-footprint',action='store_true',help='Replay frozen original-source angular footprints; implies RAW fans, no product writes')
    parser.add_argument('--signal-tracks',action='store_true',help='Diagnose measured signal-space tracks with guarded bilateral windows; no actions')
    args = parser.parse_args()
    if args.angular_probe:
        args.power_states = True
    if args.power_states:
        args.fan_joint = True
    if args.source_footprint:args.raw_fans=True
    past_cases = {}
    if args.past_sources:
        args.raw_fans = True
        receipt = json.loads(args.past_sources.read_text())
        if receipt.get('read_only') is not True:raise ValueError('past sources require read-only audit provenance')
        past_cases = {row['scan_id']:row for row in receipt['cases']}
    if args.output.resolve().is_relative_to(args.directory.resolve()):
        raise ValueError('output must be outside immutable snapshots')
    args.output.mkdir(parents=True,exist_ok=False)
    engine = ROOT/'algorithms/rainpulse_algo/radar/qc_engine'
    for name,paths in (('s_family_replay',[]),('s_family_replay.engine',[str(engine)])):
        module = types.ModuleType(name);module.__path__=paths;sys.modules[name]=module
    package = 's_family_replay.engine.review_extension.radial_revision'
    detector = importlib.import_module(package+'.raw_families')
    joint_module=importlib.import_module(package+'.family_joint') if args.joint else None
    ledger_module=importlib.import_module(package+'.source_ledger') if args.ledger or args.window_source or args.raw_fans or args.fan_joint else None
    fan_module=importlib.import_module(package+'.raw_fans') if args.raw_fans or args.fan_joint else None
    fan_joint_module=importlib.import_module(package+'.fan_joint') if args.fan_joint else None
    states_module=importlib.import_module(package+'.fan_states') if args.power_states else None
    angular_module=importlib.import_module(package+'.fan_angular') if args.angular_probe else None
    window_module=importlib.import_module(package+'.source_window') if args.window_source else None
    past_module=importlib.import_module(package+'.past_sources') if args.past_sources else None
    footprint_module=importlib.import_module(package+'.source_footprint') if args.source_footprint else None
    signal_module=importlib.import_module(package+'.signal_tracks') if args.signal_tracks else None
    rows = []
    source_sha = hashlib.sha256((engine/'review_extension/radial_revision/raw_families.py').read_bytes()).hexdigest()
    for path in sorted(args.directory.glob('*.npz')):
        with np.load(path,allow_pickle=False) as data: a={key:data[key] for key in data.files}
        meta = json.loads(str(a['METADATA']))
        fields = {'DBZH':a['RAW'],**{k[7:]:v for k,v in a.items() if k.startswith('MOMENT_')}}
        available = {k[10:]:v for k,v in a.items() if k.startswith('AVAILABLE_')}
        n=types.SimpleNamespace(shape=a['RAW'].shape,fields=fields,field_available=available,
                                ranges=a['RANGE'],azimuth=a['AZIMUTH'],geometry_good=a['GEOMETRY_GOOD'],gap_after=a['GAP_AFTER'])
        blocked = (a['WEATHER']==1)|(a['CONFLICTS']==1)|(a['RV2_BARRED_MASK']==1)
        started = time.monotonic()
        out,report = detector.detect(n,blocked,beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
        detector.validate(out,available['DBZH'],blocked)
        joint_counts={}
        if joint_module is not None or ledger_module is not None:
            snr=fields.get('SNR',np.full(n.shape,np.nan))
            reliable_source=available.get('SNR',np.zeros(n.shape,bool))&(snr>=20.)
            for name in ('RHOHV','ZDR','PHIDP'):reliable_source &= available.get(name,np.zeros(n.shape,bool))
            reliable_source &= a['RV2_WEAK_MATCH_MASK']==0
            sources=(a['SEED']==1)&reliable_source
            kinds=sources.astype('uint8')
            for bit,name in zip((2,4,8,16),('RV2_LINE_SOURCE_MASK','RV2_LINE_MORPH_MASK','RV2_LINE_ISOLATED_MASK','RV2_GROUP_MORPH_MASK')):
                path_source=a.get(name,np.zeros(n.shape))==1
                sources |= path_source;kinds |= path_source.astype('uint8')*bit
            sources &= ~blocked
        if ledger_module is not None:
            ledger,detail=ledger_module.freeze(n,blocked,sources,out['RV2_RAW_FAMILY_MASK'],
                source_kind=kinds,beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
            ledger_module.validate(ledger,available['DBZH'],blocked,sources)
            out.update(ledger);report['ledger']=detail
            joint_counts['ledger_module_sha256']=hashlib.sha256((engine/'review_extension/radial_revision/source_ledger.py').read_bytes()).hexdigest()
        if fan_module is not None:
            fans,detail=fan_module.detect(n,blocked,out['RV2_SOURCE_LEDGER_SEED_ID'],
                beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
            fan_module.validate(fans,available['DBZH'],blocked,out['RV2_SOURCE_LEDGER_SEED_ID'])
            out.update(fans);report['raw_fans']=detail
            joint_counts['raw_fan_module_sha256']=hashlib.sha256((engine/'review_extension/radial_revision/raw_fans.py').read_bytes()).hexdigest()
        if fan_joint_module is not None:
            fields,detail=fan_joint_module.qualify(n,blocked,out)
            out.update(fields);fan_joint_module.validate(out,available['DBZH'],blocked)
            report['fan_joint']=detail
            joint_counts['fan_joint_module_sha256']=hashlib.sha256((engine/'review_extension/radial_revision/fan_joint.py').read_bytes()).hexdigest()
        if states_module is not None:
            state_fields, detail = states_module.diagnose(n, blocked, out)
            out.update(state_fields)
            states_module.validate(out, available['DBZH'], blocked)
            report['fan_power_states'] = detail
            joint_counts['fan_states_module_sha256'] = hashlib.sha256((engine/'review_extension/radial_revision/fan_states.py').read_bytes()).hexdigest()
        if window_module is not None:
            windows,detail=window_module.detect(n,blocked,out,
                beam_width=meta['config']['fragment_line'].get('antenna_beam_width_deg'))
            out.update(windows);window_module.validate(out,available['DBZH'],blocked)
            report['window_source']=detail
            joint_counts['window_source_module_sha256']=hashlib.sha256((engine/'review_extension/radial_revision/source_window.py').read_bytes()).hexdigest()
        if joint_module is not None:
            joint,detail=joint_module.qualify(n,out,blocked,sources)
            out.update(joint)
            joint_module.validate(out,available['DBZH'],blocked,sources&(out['RV2_RAW_FAMILY_MASK']==1))
            report['joint']=detail
            joint_counts['joint_module_sha256']=hashlib.sha256((engine/'review_extension/radial_revision/family_joint.py').read_bytes()).hexdigest()

        nominee = out['RV2_RAW_FAMILY_MASK']==1
        r,az=a['RANGE'][None,:],a['AZIMUTH'][:,None]
        roi=(r>=250000)&(az>=285)&(az<=340) if meta['radar_id']=='z9591' else (r>=100000)&(az>=160)&(az<=280)
        remaining=a['BEFORE']&~a['ADDED']&roi
        if signal_module is not None:
            signal_fields,detail=signal_module.diagnose(n,blocked)
            out.update(signal_fields);report['signal_tracks']=detail
            joint_counts.update(signal_track_model_remaining_sector=int((remaining&(signal_fields['RV2_SIGNAL_TRACK_MODEL_MATCH_MASK']==1)).sum()),
                signal_track_module_sha256=hashlib.sha256((engine/'review_extension/radial_revision/signal_tracks.py').read_bytes()).hexdigest())
        if footprint_module is not None:
            footprint_fields,detail=footprint_module.qualify(n,blocked,out)
            out.update(footprint_fields)
            out.update(footprint_module.evidence(n))
            footprint_module.validate(out, n.field_available['DBZH'], blocked)
            detail['serialized_original_evidence_validated'] = True
            report['source_footprint']=detail
            joint_counts.update(source_footprint_qualified_remaining_sector=int((remaining&(footprint_fields['RV2_SOURCE_FOOTPRINT_QUALIFIED_MASK']==1)).sum()),
                source_footprint_module_sha256=hashlib.sha256((engine/'review_extension/radial_revision/source_footprint.py').read_bytes()).hexdigest())
        if past_module is not None:
            past=past_cases[meta['scan_id']]
            if (past['normalized_sha256']!=meta['raw_artifact_sha256'] or
                    past['sweep']!='sweep_%03d'%meta['sweep']):raise ValueError('past receipt current input differs from snapshot')
            past_fields, detail=past_module.qualify(n,blocked,out['RV2_RAW_FAN_ID'],past['references'])
            out.update(past_fields);report['past_source_joint']=detail
            joint_counts.update(past_source_candidate_remaining_sector=int((remaining&(past_fields['RV2_PAST_SOURCE_CANDIDATE_MASK']==1)).sum()),
                past_source_qualified_remaining_sector=int((remaining&(past_fields['RV2_PAST_SOURCE_QUALIFIED_MASK']==1)).sum()),
                past_source_receipt_sha256=hashlib.sha256(args.past_sources.read_bytes()).hexdigest(),
                past_source_module_sha256=hashlib.sha256((engine/'review_extension/radial_revision/past_sources.py').read_bytes()).hexdigest())
        if angular_module is not None:
            angular_fields, detail = angular_module.diagnose(n, blocked, out, target_mask=remaining)
            out.update({'RV2_FAN_ANGULAR_'+key:value for key,value in angular_fields.items()})
            report['fan_angular'] = detail
            joint_counts['fan_angular_module_sha256'] = hashlib.sha256((engine/'review_extension/radial_revision/fan_angular.py').read_bytes()).hexdigest()
            joint_counts['fan_angular_model_remaining_sector'] = int(angular_fields['MODEL_AVAILABLE_MASK'].sum())
            joint_counts['fan_angular_matched_remaining_sector'] = int(angular_fields['MATCH_MASK'].sum())
        if states_module is not None:
            joint_counts['fan_states_matched_remaining_sector'] = int((remaining & (out['RV2_FAN_STATE_MATCH_MASK']==1)).sum())
        if fan_module is not None:
            joint_counts['raw_fan_remaining_sector']=int((remaining&(out['RV2_RAW_FAN_MASK']==1)).sum())
            joint_counts['raw_fan_and_narrow_remaining_sector']=int((remaining&(out['RV2_RAW_FAN_MASK']==1)&nominee).sum())
        if fan_joint_module is not None:
            joint_counts['fan_joint_extra_remaining_sector']=int((remaining&(out['RV2_FAN_JOINT_QUALIFIED_MASK']==1)).sum())
            joint_counts['fan_joint_model_remaining_sector']=int((remaining&(out['RV2_FAN_JOINT_MODEL_AVAILABLE_MASK']==1)).sum())
        if ledger_module is not None:
            links=out['RV2_SOURCE_LEDGER_LINK_MASK']==1
            joint_counts.update(ledger_linked_remaining_sector=int((remaining&links).sum()),
                ledger_linked_remaining_total=int((a['BEFORE']&~a['ADDED']&links).sum()),
                ledger_ambiguous_sector=int((remaining&(out['RV2_SOURCE_LEDGER_LINK_HOLD']==16)).sum()))
        if window_module is not None:
            qualified=out['RV2_SOURCE_WINDOW_QUALIFIED_MASK']==1
            joint_counts.update(window_extra_remaining_sector=int((remaining&qualified).sum()),
                window_extra_remaining_total=int((a['BEFORE']&~a['ADDED']&qualified).sum()),
                window_candidate_remaining_sector=int((remaining&(out['RV2_SOURCE_WINDOW_CANDIDATE_MASK']==1)).sum()))
        if joint_module is not None:
            qualified=out['RV2_FAMILY_JOINT_QUALIFIED_MASK']==1
            joint_counts.update(joint_extra_visible=int((a['BEFORE']&~a['ADDED']&qualified).sum()),
                                joint_extra_sector=int((remaining&qualified).sum()),
                                joint_anchored_sector=int((remaining&(out['RV2_FAMILY_JOINT_ANCHORED_MASK']==1)).sum()),
                                joint_polar_sector=int((remaining&(out['RV2_FAMILY_JOINT_POLAR_MASK']==1)).sum()))
        row={'case':path.stem,'input_snapshot_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
             'module_sha256':source_sha,'scan_id':meta['scan_id'],'scope':'raw_nomination_only_not_QC_product',
             'remaining_sector':int(remaining.sum()),'remaining_nominated':int((remaining&nominee).sum()),
             'nomination_gates':int(nominee.sum()),'objects':report['objects'],'status':report['status'],
             'missing_or_protected_nominees':int((nominee&(~available['DBZH']|blocked)).sum()),
             'elapsed_seconds':round(time.monotonic()-started,3),'action_gates':0,'filled_gates':0,
             'raw_unchanged':bool(np.array_equal(n.fields['DBZH'],a['RAW'],equal_nan=True)),
             'independent_weather_truth':False,'detail':report,**joint_counts}
        assert row['missing_or_protected_nominees']==0 and row['raw_unchanged']
        np.savez_compressed(args.output/(path.stem+'.npz'),METADATA=np.array(json.dumps(row)),**out)
        rows.append(row)
        print(json.dumps({k:v for k,v in row.items() if k!='detail'}),flush=True)
    (args.output/'report.json').write_text(json.dumps(rows,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':main()
