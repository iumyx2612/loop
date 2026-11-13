"""Run write_code's stages 1-5 (read, forward fill, joins, select, normalize) on saved explore runs. No LLM, no .env.

Prints per shop the stage 5 failure report: per column its kind, filled cells, failed cells and the most frequent
failing values (count, first row, error). Where a reference conversion exists (data_std/<split>/<shop>/
sales_lines.parquet), the format columns (dates, times, money, quantities, unit) are compared with it line by line
(matched on source_file / source_sheet / source_row): a dev check against the answer key, for finding
wrong-but-successful parses. Reference values also carry stage 6-8 work (return lines' order_date, implied
discounts, units from conversions), so some differences are expected there.

Usage (the run folder before --shops):
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_5.py runs/explore/v2/9B/train/step_9_conversion
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_5.py <run folder> --shops biz_108 --out-dir runs/write_code/x
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # run_stages_1_4 (puts v2_runner and ROOT on sys.path)

from run_stages_1_4 import Tee, plan_of  # noqa: E402  (first: it puts v2_runner on sys.path)

import pandas as pd  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.write_code import KINDS, apply_stages_1_4, normalize  # noqa: E402

COMPARED = ("date", "datetime", "money", "qty")  # kinds compared with the reference, plus the unit column
KEYS = ["source_file", "source_sheet", "source_row"]


def same(kind: str, a: pd.Series, b: pd.Series) -> pd.Series:
    both_null = a.isna() & b.isna()
    if kind in ("date", "datetime"):
        x, y = pd.to_datetime(a, errors="coerce"), pd.to_datetime(b, errors="coerce")
        if kind == "date":
            x, y = x.dt.normalize(), y.dt.normalize()
        return (x == y) | both_null
    if kind in ("money", "qty"):
        x, y = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
        return ((x - y).abs() < 0.5) | both_null
    return (a.astype("string") == b.astype("string")).fillna(False) | both_null


def compare(lines: pd.DataFrame, ref_path: Path) -> None:
    ref = pd.read_parquet(ref_path)
    cols = [c for c in lines.columns if (KINDS.get(c) in COMPARED or c == "unit") and c in ref.columns]
    ours = lines[KEYS + cols].copy()
    for df in (ours, ref):
        df["source_file"] = df["source_file"].astype(str)
        df["source_sheet"] = df["source_sheet"].fillna("").astype(str)
        df["source_row"] = pd.to_numeric(df["source_row"]).astype("Int64")
    # common.read_sqlite numbers the first data row 1; the explore tools (and so the pipeline) number the column
    # names row 1, as for a csv, so the first data row is 2
    sqlite = ref["source_file"].str.lower().str.endswith(".sqlite")
    ref.loc[sqlite, "source_row"] = ref.loc[sqlite, "source_row"] + 1
    m = ours.merge(ref[KEYS + cols], on=KEYS, how="inner", suffixes=("", "_ref"))
    print(f"  vs reference: {len(m):,} of {len(lines):,} lines matched by source row (reference has {len(ref):,})")
    if not len(m):
        return
    for c in cols:
        ok = same(KINDS.get(c, "label"), m[c], m[c + "_ref"])
        bad = m[~ok]
        line = f"    {c:<14} {ok.mean():>7.2%} agree"
        if len(bad):
            ex = [f"{r.source_file} [{r.source_sheet}] row {r.source_row}: ours {getattr(r, c)!r} ref "
                  f"{getattr(r, c + '_ref')!r}" for r in bad.head(3).itertuples()]
            line += f" ({len(bad):,} differ) e.g.\n        " + "\n        ".join(ex)
        print(line)


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
    except Exception as e:  # noqa: BLE001 - one shop's failure must not stop the others
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return
    print(f"  {len(norm.lines):,} lines in {time.perf_counter() - start:.1f}s")
    clean = []
    for r in norm.columns.values():
        if not r.failed and not r.note and not r.sheets:
            clean.append(r.column)
            continue
        head = f"    {r.column:<14} {r.kind:<13}"
        if r.failed:
            head += f" {r.failed:,} of {r.filled:,} cells FAILED ({r.failed / r.filled:.2%})"
        elif r.sheets:
            head += f" no failure ({r.filled:,} cells)"
        print(head + (f"  [{r.note}]" if r.note else ""))
        decided = [s for s in r.sheets if "NO YEAR" not in s]
        if decided:
            months = sum("checked" in s for s in decided)
            print(f"        year-less dates: year decided on {len(decided)} sheet(s) (sheet / file name + full dates "
                  f"agree), month checked on {months}; e.g. {decided[0]}")
        for s in r.sheets:
            if "NO YEAR" in s:
                print(f"        year-less dates: {s}")
        for value, n, where, error in r.failures:
            print(f"        {n:>6,} x {value}  (first: {where})  {error}")
    if clean:
        print(f"    parsed with no failure: {', '.join(clean)}")
    ref = v2_runner.ROOT / "data_std" / split / shop / "sales_lines.parquet"
    if ref.exists():
        compare(norm.lines, ref)


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
        out = open(Path(args.out_dir) / "_summary.txt", "w", encoding="utf-8", buffering=1)  # line by line: readable while running
        sys.stdout = Tee(sys.__stdout__, out)
        sys.stderr = Tee(sys.__stderr__, out)
        print(f"stages 1-5 on {', '.join(args.paths)}")
    try:
        for split, shop, state in jobs:
            report(split, shop, state)
    finally:
        if out:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            out.close()


if __name__ == "__main__":
    main()
