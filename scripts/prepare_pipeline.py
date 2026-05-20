from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sentinel_blocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare Avant Sentinel/S2DR4 pipeline outputs")
    parser.add_argument("--reference-date", default="2026-05-19")
    parser.add_argument("--months", type=int, default=3)
    parser.add_argument("--max-cloud", type=float, default=5.0)
    parser.add_argument("--farm-slug", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-gee-check", action="store_true")
    parser.add_argument("--skip-sentinel", action="store_true")
    args = parser.parse_args()

    print("[pipeline] Preparing 4x4 km blocks")
    manifest = sentinel_blocks.generate_blocks(write_files=True)
    print(json.dumps({
        "export_dir": manifest["export_dir"],
        "farm_count": manifest["farm_count"],
        "block_count": manifest["block_count"],
    }, ensure_ascii=False, indent=2))

    if not args.skip_gee_check:
        print("[pipeline] Checking Earth Engine auth")
        print(json.dumps(sentinel_blocks.check_earth_engine(), ensure_ascii=False, indent=2))

    if not args.skip_sentinel:
        print("[pipeline] Searching Sentinel-2 scenes")
        summary = sentinel_blocks.search_sentinel(
            reference_date=args.reference_date,
            months=args.months,
            max_cloud=args.max_cloud,
            farm_slug=args.farm_slug,
            limit=args.limit,
        )
        print(json.dumps({
            "queried_blocks": summary["queried_blocks"],
            "matches_under_cloud": summary["matches_under_cloud"],
            "fallback_matches": summary["fallback_matches"],
        }, ensure_ascii=False, indent=2))

    print("[pipeline] Creating S2DR4 queue")
    queue = sentinel_blocks.prepare_superres_queue(farm_slug=args.farm_slug)
    print(json.dumps(queue, ensure_ascii=False, indent=2))

    queue_path = Path(queue["queue_path"])
    print(f"[pipeline] Queue ready: {queue_path}")


if __name__ == "__main__":
    main()
