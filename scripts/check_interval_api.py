#!/usr/bin/env python3
"""Read-only deployed interval smoke check; never starts model/regeneration jobs."""

import argparse
import json
import time
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cycle-id", required=True)
    args = parser.parse_args()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def read(path, payload=None):
        request = urllib.request.Request(
            args.base_url.rstrip("/") + path,
            data=json.dumps(payload).encode() if payload else None,
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=125) as response:
            return response.read()

    results = {}
    detail = json.loads(read("/api/v1/workspace/cycles/" + args.cycle_id))
    for start, end in ((0, 60), (60, 120), (0, 120), (15, 45)):
        for algorithm in ("qpe", "lk", "steps", "nowcastnet"):
            tick = time.monotonic()
            data = json.loads(
                read(
                    "/api/v1/workspace/accumulations",
                    dict(
                        cycle_id=args.cycle_id,
                        algorithm=algorithm,
                        start_minutes=start,
                        end_minutes=end,
                    ),
                )
            )
            panel = data["panels"][0]
            result = dict(
                algorithm=algorithm,
                interval=[start, end],
                seconds=round(time.monotonic() - tick, 3),
                status=panel["status"],
            )
            if panel["frames"]:
                frame = panel["frames"][0]
                assert frame["unit"] == "mm"
                assert frame["source_leads"] == list(range(start + 5, end + 1, 5))
                assert read(frame["image_url"]).startswith(b"\x89PNG")
                query = urllib.parse.urlencode(
                    dict(asset_url=frame["image_url"], longitude=119.5, latitude=26.5)
                )
                sample = json.loads(read("/api/v1/workspace/sample?" + query))
                assert sample["unit"] == "mm"
                result.update(
                    value=sample.get("value"),
                    valid=sample["valid"],
                    coverage=frame["coverage_ratio"],
                )
                results[algorithm, start, end] = sample
            else:
                result["reason"] = panel.get("unavailable_reason")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    for algorithm in ("qpe", "lk", "nowcastnet"):
        samples = [
            results.get((algorithm, start, end))
            for start, end in ((0, 60), (60, 120), (0, 120))
        ]
        if all(sample and sample["valid"] for sample in samples):
            assert (
                abs(samples[0]["value"] + samples[1]["value"] - samples[2]["value"])
                < 1e-4
            )
            print(f"{algorithm}: interval additivity verified", flush=True)
        raw_panel = next(
            panel for panel in detail["panels"] if panel["panel_id"] == algorithm
        )
        raw_values = []
        for lead in range(20, 46, 5):
            frame = next(
                frame
                for frame in raw_panel["frames"]
                if frame["lead_time_minutes"] == lead
            )
            query = urllib.parse.urlencode(
                dict(
                    asset_url=frame["image_url"],
                    longitude=119.5,
                    latitude=26.5,
                )
            )
            raw = json.loads(read("/api/v1/workspace/sample?" + query))
            if raw["valid"]:
                raw_values.append(raw["value"])
        actual = results.get((algorithm, 15, 45))
        if len(raw_values) == 6:
            assert (
                actual
                and actual["valid"]
                and abs(sum(raw_values) / 12 - actual["value"]) < 1e-4
            )
            print(f"{algorithm}: direct raw-rate integration verified", flush=True)
    assert len(results) == 16, "some expected interval products are unavailable"


if __name__ == "__main__":
    main()
