"""Run write_code's stages 1-6 (read, forward fill, joins, select, normalize, rules) on saved explore runs. No LLM.

Prints per shop the stage 6 report (status words not in the plan, blank_status lines, the order-level spread and
its conflicts, return lines, service-fee lines, dropped non-revenue and duplicate lines). Where a reference
conversion exists (data_std/<split>/<shop>/sales_lines.parquet), compares with it: line counts, lines only in ours
/ only in the reference (by source_*), and per column (status, is_return_line, return_date, order_date,
order_time, line_kind) the agreement on the matched lines.

Usage (the run folder before --shops):
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_6.py runs/explore/v2/9B/public_test/step_9_conversion
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_6.py <run folder> --shops dev_004 --out-dir runs/write_code/x
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_stages_1_4 import Tee, plan_of  # noqa: E402  (first: it puts v2_runner on sys.path)
from run_stage_5 import KEYS, same  # noqa: E402

import pandas as pd  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.write_code import apply_rules, apply_stages_1_4, normalize  # noqa: E402

COMPARED = {"status": "label", "is_return_line": "label", "line_kind": "label", "return_date": "date",
            "order_date": "date", "order_time": "datetime"}


def keyed(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["source_file"] = df["source_file"].astype(str)
    df["source_sheet"] = df["source_sheet"].fillna("").astype(str)
    df["source_row"] = pd.to_numeric(df["source_row"]).astype("Int64")
    return df


def compare(lines: pd.DataFrame, ref_path: Path) -> None:
    ref = keyed(pd.read_parquet(ref_path))
    sqlite = ref["source_file"].str.lower().str.endswith(".sqlite")
    ref.loc[sqlite, "source_row"] = ref.loc[sqlite, "source_row"] + 1  # see run_stage_5.compare
    ours = keyed(lines)
    m = ours.merge(ref, on=KEYS, how="outer", suffixes=("", "_ref"), indicator=True)
    only_ours, only_ref = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    print(f"  vs reference: ours {len(ours):,} lines, reference {len(ref):,}; only ours {len(only_ours):,}, "
          f"only reference {len(only_ref):,}")
    for what, part in (("only ours", only_ours), ("only reference", only_ref)):
        for r in part.head(3).itertuples():
            print(f"      {what}: {r.source_file} [{r.source_sheet}] row {r.source_row}")
    both = m[m["_merge"] == "both"]
    if not len(both):
        return
    for c, kind in COMPARED.items():
        if c not in ours.columns or c not in ref.columns:
            print(f"    {c:<15} {'not in ours' if c not in ours.columns else 'not in the reference'}")
            continue
        a, b = both[c], both[c + "_ref"]
        if c == "is_return_line":
            a, b = a.fillna(False).astype(bool).astype(str), b.fillna(False).astype(bool).astype(str)
        ok = same(kind, a, b)
        bad = both[~ok]
        line = f"    {c:<15} {ok.mean():>8.3%} agree"
        if len(bad):
            ex = [f"{r.source_file} [{r.source_sheet}] row {r.source_row}: ours {getattr(r, c)!r} ref "
                  f"{getattr(r, c + '_ref')!r}" for r in bad.head(3).itertuples()]
            line += f" ({len(bad):,} differ) e.g.\n        " + "\n        ".join(ex)
        print(line)


def print_report(r) -> None:
    print(f"  stage 6: {r.lines_in:,} lines in, {r.lines_out:,} out")
    if r.status_column:
        if r.unknown_words:
            print(f"    status: {sum(n for _, n in r.unknown_words):,} lines with words not in the plan: "
                  f"{r.unknown_words[:5]}")
        if r.blank_status_lines:
            print(f"    status: {r.blank_status_lines:,} lines of orders with no word -> blank_status")
        if r.no_status_lines:
            print(f"    status: {r.no_status_lines:,} lines of orders with no word, blank_status None -> null")
    else:
        print("    status: no status column")
    for c, s in r.spread.items():
        print(f"    spread {c}: {s['cells']:,} blank cells filled in {s['orders']:,} orders")
    for c, s in r.conflicts.items():
        print(f"    CONFLICT {c}: {s['orders']:,} orders with two values, e.g. {s['examples'][:2]}")
    if r.return_lines:
        print(f"    return lines {r.return_lines:,} in {r.return_orders:,} orders; status set to returned on "
              f"{r.returned_overwritten:,} lines that said otherwise; orders with only return lines "
              f"{r.only_return_orders:,}" + (f"; FLAGGED ONLY (plan says no returns): {r.flagged_only:,}"
                                               if r.flagged_only else "")
              + (f"; without order id {r.no_order_returns:,}" if r.no_order_returns else ""))
    if r.service_fee_lines:
        print(f"    service-fee lines {r.service_fee_lines:,}")
    if r.non_revenue_dropped:
        print(f"    non-revenue dropped {sum(n for _, n in r.non_revenue_dropped):,}: {r.non_revenue_dropped[:5]}")
    if r.duplicates_dropped:
        print(f"    duplicates dropped {r.duplicates_dropped:,}, e.g. orders {r.duplicate_examples}")


def report(split: str, shop: str, state: dict) -> None:
    print(f"\n## {shop}")
    plan = plan_of(state)
    if plan is None:
        print("  no plan in the saved state")
        return
    start = time.perf_counter()
    try:
        applied = apply_stages_1_4(Path(state["shop_dir"]), plan)
        norm = normalize(applied.lines, plan)
        ruled = apply_rules(norm.lines, applied.raw_lines, plan)
    except Exception as e:  # noqa: BLE001 - one shop's failure must not stop the others
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return
    print(f"  {time.perf_counter() - start:.1f}s")
    print_report(ruled.report)
    ref = v2_runner.ROOT / "data_std" / split / shop / "sales_lines.parquet"
    if ref.exists():
        compare(ruled.lines, ref)


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
        print(f"stages 1-6 on {', '.join(args.paths)}")
    try:
        for split, shop, state in jobs:
            report(split, shop, state)
    finally:
        if out:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            out.close()


if __name__ == "__main__":
    main()
