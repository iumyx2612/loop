"""Benchmark agent conversions against the hand-written references.

Compares data_std_agent/<split>/<shop>/ (candidate) with data_std/<split>/<shop>/ (reference), check by check
(see src/datamate/bench.py), prints a per-shop report and writes runs/bench/<timestamp>[-label].json.
The previous JSON of the same split is used to show how each shop's score changed.

Usage: .venv/Scripts/python.exe -I scripts/bench_analyst.py [--split train|public_test] [--label X]
           [--candidate DIR] [--self] [--verbose] [shop ...]
       (default: every shop of the split that has a reference; --self compares the reference with itself)
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from src.datamate import bench  # noqa: E402

OUT_DIR = ROOT / "runs" / "bench"


def load(shop_dir: Path) -> tuple[pd.DataFrame, dict]:
    df = pd.read_parquet(shop_dir / "sales_lines.parquet")
    m = json.loads((shop_dir / "manifest.json").read_text(encoding="utf-8"))
    return df, m


def bench_shop(ref_dir: Path, cand_dir: Path) -> dict:
    if not (cand_dir / "sales_lines.parquet").exists():
        return {"status": "missing", "score": 0.0, "checks": []}
    try:
        ref, ref_m = load(ref_dir)
        cand, cand_m = load(cand_dir)
        checks = bench.compare(cand, ref, cand_m, ref_m)
    except Exception as e:  # a broken candidate must not stop the run
        return {"status": "error", "score": 0.0, "error": f"{type(e).__name__}: {e}", "checks": []}
    return {"status": "ok", "score": round(bench.shop_score(checks), 4), "checks": bench.to_json(checks)}


def previous_run(split: str, before: Path) -> dict | None:
    runs = sorted(p for p in OUT_DIR.glob("*.json") if p != before)
    for p in reversed(runs):
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("split") == split:
            return data | {"file": p.name}
    return None


def main(args) -> None:
    ref_root = ROOT / "data_std" / args.split
    cand_root = ref_root if args.self else Path(args.candidate) / args.split
    shops = args.shops or sorted(p.name for p in ref_root.iterdir() if (p / "sales_lines.parquet").exists())

    started = dt.datetime.now()
    name = started.strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else "")
    out_path = OUT_DIR / f"{name}.json"
    prev = previous_run(args.split, out_path)
    prev_scores = {s: r["score"] for s, r in (prev or {}).get("shops", {}).items()}

    results = {}
    for shop in shops:
        r = bench_shop(ref_root / shop, cand_root / shop)
        results[shop] = r
        delta = f"  ({r['score'] - prev_scores[shop]:+.3f})" if shop in prev_scores else ""
        print(f"## {shop}  {r['status']}  score {r['score']:.3f}{delta}")
        if r.get("error"):
            print(f"   {r['error']}")
        for c in r["checks"]:
            if c["score"] < 1 or args.verbose:
                print(f"   {c['score']:.3f}  {c['name']}: {c['summary']}")
                for e in c["examples"]:
                    print(f"          {e}")

    mean = sum(r["score"] for r in results.values()) / len(results) if results else 0.0
    report = {
        "started": started.isoformat(timespec="seconds"),
        "label": args.label,
        "split": args.split,
        "candidate": "reference (--self)" if args.self else str(cand_root.relative_to(ROOT) if cand_root.is_relative_to(ROOT) else cand_root),
        "mean_score": round(mean, 4),
        "shops": results,
    }
    vs = f", previous {prev['mean_score']:.3f} ({prev['file']})" if prev else ""
    print(f"\nmean score {mean:.3f} over {len(results)} shops{vs}")
    if args.self:  # a sanity run: keep it out of the history so it never becomes the "previous" run
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"written {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="train", choices=["train", "public_test"])
    p.add_argument("--candidate", default=str(ROOT / "data_std_agent"), help="root holding <split>/<shop>/")
    p.add_argument("--self", action="store_true", help="compare each reference with itself (must score 1.0)")
    p.add_argument("--label", default="", help="suffix of the JSON file name, e.g. the agent version")
    p.add_argument("--verbose", action="store_true", help="also list the checks that passed")
    p.add_argument("shops", nargs="*")
    main(p.parse_args())
