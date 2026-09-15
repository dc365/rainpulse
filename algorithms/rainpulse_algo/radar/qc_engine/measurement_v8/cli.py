"""Local-only CLI; no NATS, database, network, deployment or automatic promotion."""

import argparse
import json
from pathlib import Path

import zarr

from .assessment import assess
from .forest import train
from .io import file_hash, safe, tree_hash
from .schema import Case, load_config
from .workbench import extract_case, infer_case


def freeze(args):
    root = Path(args.root).resolve()
    refs = {}
    for key in ("normalized", "qc", "profile", "flags", "context"):
        name = getattr(args, key)
        if name is None:
            continue
        p = (root / name).resolve()
        if not p.is_relative_to(root):
            raise ValueError("all frozen inputs must be inside --root")
        refs[key] = {
            "path": p.relative_to(root).as_posix(),
            "sha256": tree_hash(p) if key in {"normalized", "qc"} else file_hash(p),
        }
    q = zarr.open_group(str(root / refs["qc"]["path"]), mode="r")
    spec = Case.model_validate(
        {
            **refs,
            "case_id": args.case_id,
            "process_id": args.process,
            "partition": args.partition,
            "data_kind": args.data_kind,
            "expected_scan_id": q.attrs.get("scan_id"),
            "expected_radar_id": q.attrs.get("radar_id"),
            "expected_qc_asset_id": q.attrs.get("asset_id"),
            "expected_context_fingerprint": q.attrs.get("context_fingerprint"),
            "sweeps": args.sweeps.split(","),
        }
    )
    path = root / args.name
    if path.parent != root or path.exists():
        raise ValueError("new manifest filename directly inside root required")
    with path.open("x", encoding="utf-8") as f:
        f.write(spec.model_dump_json(indent=2))
    # Validate the actual binding immediately; retain a bad manifest for diagnosis
    # rather than quietly manufacturing metadata to make it pass.
    from .case import FrozenCase

    case = FrozenCase(path)
    for cut in spec.sweeps:
        case.sweep(cut)
    return {"manifest": str(path), "sha256": file_hash(path), "published": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hash")
    h.add_argument("path", type=Path)
    h.add_argument("--directory", action="store_true")
    f = sub.add_parser("freeze")
    f.add_argument("--root", required=True)
    for key in ("normalized", "qc", "profile", "flags"):
        f.add_argument("--" + key, required=True)
    f.add_argument("--context")
    f.add_argument("--case-id", required=True)
    f.add_argument("--process", required=True)
    f.add_argument(
        "--partition", choices=["train", "calibrate", "validate", "inspect"], required=True
    )
    f.add_argument("--data-kind", choices=["real", "synthetic"], required=True)
    f.add_argument("--sweeps", required=True, help="comma separated original cut names")
    f.add_argument("--name", default="case.json")
    for command in ("extract", "infer"):
        x = sub.add_parser(command)
        x.add_argument("--case", required=True)
        x.add_argument("--config", required=True)
        x.add_argument("--output", required=True)
        x.add_argument("--native-binary")
        x.add_argument("--native-sha256")
        if command == "infer":
            x.add_argument("--model", required=True)
            x.add_argument("--model-sha256", required=True)
            x.add_argument("--review")
            x.add_argument("--no-png", action="store_true")
    t = sub.add_parser("train")
    t.add_argument("--dataset", required=True)
    t.add_argument("--config", required=True)
    t.add_argument("--output", required=True)
    a = sub.add_parser("assess")
    a.add_argument("--manifest", required=True)
    a.add_argument("--output", required=True)
    args = p.parse_args(argv)
    if args.command == "hash":
        report = {
            "path": str(args.path),
            "sha256": tree_hash(args.path) if args.directory else file_hash(args.path),
            "scheme": "relative-path-file-sha256-v1" if args.directory else "sha256",
        }
    elif args.command == "freeze":
        report = freeze(args)
    elif args.command == "train":
        report = train(args.dataset, load_config(args.config).training, args.output)
    elif args.command == "assess":
        report = assess(args.manifest, args.output)
    else:
        kw = dict(binary=args.native_binary, binary_sha=args.native_sha256)
        cfg = load_config(args.config)
        if args.command == "extract":
            report = extract_case(args.case, cfg, args.output, **kw)
        else:
            report = infer_case(
                args.case,
                cfg,
                args.model,
                args.model_sha256,
                args.output,
                review=args.review,
                render=not args.no_png,
                **kw,
            )
    # stdout is a bounded completion summary, not a full feature/graph payload.
    print(
        json.dumps(
            safe(
                {
                    "command": args.command,
                    "output": getattr(args, "output", None),
                    "published": False,
                    "status": "completed",
                    **({"sha256": report["sha256"]} if "sha256" in report else {}),
                }
            ),
            ensure_ascii=False,
        )
    )
    return report


if __name__ == "__main__":
    main()
