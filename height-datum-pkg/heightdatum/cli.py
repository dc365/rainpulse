"""命令行入口。

  python -m heightdatum point <lon> <lat> <h_1985> [--grid data/egm2008_fujian_2p5.npy]
  python -m heightdatum csv stations_input.csv -o out.csv [--grid ...]
  python -m heightdatum gnss <lon> <lat> <h_gnss> <h_1985_recorded> [--grid ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from .convert import egm2008_from_1985, uncertainty_budget
from .geoid_grid import GeoidGrid
from .gnss_anchor import anchor_report
from .offsets import DEFAULT_OFFSET_M, DEFAULT_OFFSET_SIGMA_M

DEFAULT_GRID = os.path.join(os.path.dirname(__file__), "..", "data", "egm2008_fujian_2p5.npy")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="heightdatum", description="1985 国家高程基准 ↔ EGM2008 转换")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("point", "csv", "gnss"):
        sp = sub.add_parser(name)
        sp.add_argument("--grid", default=DEFAULT_GRID, help="EGM2008 N 格网（.npy/.tif/.pgm）")
        sp.add_argument("--offset", type=float, default=None, help="覆盖 δ 常数（米）")
    sub.choices["point"].add_argument("lon", type=float)
    sub.choices["point"].add_argument("lat", type=float)
    sub.choices["point"].add_argument("h1985", type=float)
    sub.choices["csv"].add_argument("input_csv")
    sub.choices["csv"].add_argument("-o", "--output", required=True)
    sub.choices["gnss"].add_argument("lon", type=float)
    sub.choices["gnss"].add_argument("lat", type=float)
    sub.choices["gnss"].add_argument("h_gnss", type=float)
    sub.choices["gnss"].add_argument("h1985_recorded", type=float)
    args = p.parse_args(argv)

    grid = GeoidGrid.load(args.grid)
    offset = args.offset if args.offset is not None else DEFAULT_OFFSET_M

    if args.cmd == "point":
        h = egm2008_from_1985(args.h1985, args.lon, args.lat, offset_m=offset)
        n = grid.sample(args.lon, args.lat)
        print(json.dumps({
            "lon": args.lon, "lat": args.lat, "N_egm2008_m": round(n, 4),
            "delta_m": offset, "h_1985_m": args.h1985, "h_egm2008_m": round(h, 3),
            "sigma_m": uncertainty_budget(method="literature")["total_rss"],
        }, ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "csv":
        with open(args.input_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        out_rows = []
        for r in rows:
            lon, lat = float(r["lon_deg"]), float(r["lat_deg"])
            h85 = float(r["antenna_h_1985_m"])
            out_rows.append({**r, "N_egm2008_m": round(grid.sample(lon, lat), 4),
                             "delta_m": offset,
                             "antenna_h_egm2008_m": round(egm2008_from_1985(h85, lon, lat,
                                                                          offset_m=offset), 3),
                             "sigma_m": DEFAULT_OFFSET_SIGMA_M})
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)
        print(f"written: {args.output} ({len(out_rows)} rows)")
        return 0

    rep = anchor_report("cli", args.lon, args.lat, args.h_gnss, args.h1985_recorded,
                        geoid_grid=grid, literature_offset_m=offset)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
