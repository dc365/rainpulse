#!/usr/bin/env python3
"""Inspect surface files as prospective controls, without assigning rain truth.

Observed storage: base64 -> one complete ZIP member -> station/value CSV.
Filename clocks, units, accumulation columns and QC codes remain unverified.
This read-only inventory never changes a radar mask or promotes acceptance.
"""
import argparse
import base64
from collections import Counter
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

MAX_BYTES = 8 * 1024**2


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('surface object exceeds inspection budget')
    packed = base64.b64decode(raw.strip(), validate=True)
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].is_dir() or members[0].file_size > MAX_BYTES:
            raise ValueError('surface ZIP must contain one bounded data member')
        text = archive.read(members[0]).decode('utf-8')  # includes ZIP CRC verification
    rows = {}
    widths = set()
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[0] or row[0] in rows:
            raise ValueError('missing or duplicate surface station identity')
        values = [None if value == '' else float(value) for value in row[1:]]
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError('nonfinite surface value')
        widths.add(len(values))
        rows[row[0]] = values
    if not rows or len(widths) != 1:
        raise ValueError('empty or inconsistent surface table')
    return rows


def inspect(root, hour):
    evidence = []

    def read(clock, field):
        relative = Path(clock.strftime('%Y/%m/%d')) / (clock.strftime('%Y%m%d%H%M') + '_' + field)
        path = root / relative
        record = {'relative_path': str(relative), 'filename_clock': clock.isoformat(),
                  'clock_timezone': 'UNVERIFIED', 'field': field}
        evidence.append(record)
        if not path.exists():
            record['state'] = 'MISSING'
            return None
        try:
            if path.stat().st_size > MAX_BYTES:
                raise ValueError('surface object exceeds inspection budget')
            raw = path.read_bytes()
            record.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
            rows = decode(raw)
            record.update(state='FORMAT_VERIFIED', stations=len(rows),
                          columns=len(next(iter(rows.values()))),
                          first_column_distribution=dict(Counter(str(v[0]) for v in rows.values())))
            return rows
        except (ValueError, zipfile.BadZipFile, UnicodeError, OSError) as exc:
            record.update(state='INVALID', reason=str(exc))
            return None

    minutes = [read(hour + timedelta(minutes=i), 'RAIN_ONEMINUTE') for i in range(1,61)]
    end = hour + timedelta(hours=1)
    accumulated = read(end, 'RAIN_SUM')
    read(end, 'QC_RAIN_SUM')
    comparison = {'hypothesis': 'first RAIN_SUM column equals preceding 60 minute values in identical units',
                  'hypothesis_status': 'UNVERIFIED', 'complete_minute_files': sum(m is not None for m in minutes)}
    if all(m is not None for m in minutes) and accumulated is not None:
        common = set(accumulated).intersection(*(set(m) for m in minutes))
        checked = []
        for sid in sorted(common):
            values = [m[sid][0] for m in minutes]
            if any(v is None for v in values) or accumulated[sid][0] is None:
                continue
            total = math.fsum(values)
            checked.append((sid, total, accumulated[sid][0]))
        differences = [(sid, a, b) for sid, a, b in checked if abs(a-b) > .15]
        comparison.update(stations_compared=len(checked), differences_above_point15=len(differences),
                          examples=[{'station':sid, 'minute_sum_raw_units':a, 'accumulation_first_column_raw_units':b}
                                    for sid,a,b in differences[:10]])
    return {'state': 'UNVERIFIED_CONTROL_CONTRACT', 'files':evidence, 'arithmetic_probe':comparison,
            'fujian_station_dictionary_present': any((root / ('StationInfo_350000_' + kind)).is_file()
                                                    for kind in ('county','area','all')),
            'missing_contracts':['provenance_real_observation', 'clock_timezone', 'value_units',
                                 'accumulation_column_intervals', 'qc_code_meanings', 'fujian_station_geometry'],
            'may_be_used_as_weather_truth':False, 'radar_algorithm_or_product_changed':False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('filename_hour', help='YYYY-MM-DDTHH:00; filename clock only, no timezone inferred')
    p.add_argument('output', type=Path)
    a = p.parse_args()
    hour = datetime.fromisoformat(a.filename_hour)
    if hour.tzinfo is not None or hour.minute or hour.second or hour.microsecond:
        raise ValueError('whole filename hour without an inferred timezone required')
    result = inspect(a.root, hour)
    with a.output.open('x') as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}))


if __name__ == '__main__':
    main()
