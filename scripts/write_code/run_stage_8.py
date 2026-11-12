"""Run write_code's whole pipeline (stages 1-8) on saved explore runs and write data_std_agent/. No LLM, no .env.

Per shop: stages 1-8, then sales_lines.parquet + manifest.json to data_std_agent/<split>/<shop>/ (never over the
references in data_std/). Prints the stage 8 report: where product keys came from, customer keys, implied
discount, data_end and its source, and schema.validate's problems. Then compare with the references:
  .venv/Scripts/python.exe -I scripts/bench_analyst.py --split <split> --candidate data_std_agent

Usage (the run folder before --shops):
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_8.py runs/explore/v2/9B/public_test/step_7B_conversion
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_8.py <run folder> --shops dev_004 --out-dir runs/write_code/x
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_stages_1_4 import Tee, plan_of  # noqa: E402  (first: it puts v2_runner on sys.path)

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.write_code import (apply_rules, apply_stages_1_4, apply_units,  # noqa: E402
                                                                     finish, normalize, write)

OUT_ROOT = v2_runner.ROOT / "data_std_agent"


def report(split: str, shop: str, state: dict) -> None:
    print(f"\n## {shop}")
    plan = plan_of(state)
    if plan is None:
        print("  no plan in the saved state")
        return
    start = time.perf_counter()
    try:
        shop_dir = Path(state["shop_dir"])
        applied = apply_stages_1_4(shop_dir, plan)
        norm = normalize(applied.lines, plan)
        ruled = apply_rules(norm.lines, applied.raw_lines, plan)
        units = apply_units(ruled.lines, shop_dir, plan)
        done = finish(units.lines, plan, applied.joined, shop_dir)
        m = write(done, OUT_ROOT / split / shop, shop)
    except Exception as e:  # noqa: BLE001 - one shop's failure must not stop the others
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return
    r = done.report
    print(f"  {m['rows']:,} lines, {m['orders'] or 0:,} orders in {time.perf_counter() - start:.1f}s -> "
          f"data_std_agent/{split}/{shop}")
    print(f"    product_key: " + ", ".join(f"{k} {n:,}" for k, n in r.product_key.items() if n)
          + (f"; {r.names_changed:,} names set to the table's / most frequent spelling" if r.names_changed else ""))
    print(f"    customer_key: by {r.customer_key}; line_discount: {'implied' if r.implied_discount else 'column'}")
    print(f"    data_end {r.data_end} ({r.data_end_from}); dates {m['date_min']} .. {m['date_max']}")
    print(f"    missing columns: {', '.join(m['columns_missing']) or '-'}")
    for p in r.problems:
        print(f"    VALIDATE {p}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved plan (after write_plan)")
    p.add_argument("--shops", nargs="*", default=None)
    p.add_argument("--out-dir", default=None, help="also write the report to <out-dir>/_summary.txt")
    args = p.parse_args()
    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    out = None
    if args.out_dir:
        Path(args.out_dir).mkdir(parents=True, exist_ok=True)
        out = open(Path(args.out_dir) / "_summary.txt", "w", encoding="utf-8", buffering=1)
        sys.stdout = Tee(sys.__stdout__, out)
        sys.stderr = Tee(sys.__stderr__, out)
        print(f"stages 1-8 on {', '.join(args.paths)}")
    try:
        for split, shop, state in jobs:
            report(split, shop, state)
    finally:
        if out:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            out.close()


if __name__ == "__main__":
    main()
