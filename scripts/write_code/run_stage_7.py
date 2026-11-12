"""Run write_code's stages 1-7 (… rules, units and conversions) on saved explore runs. No LLM, no .env.

Prints per shop the stage 7 report (conversion sources read, lines per case, cross-check disagreements, lines left
without a factor or a unit) and, where a reference conversion exists, the agreement on unit, base_unit,
unit_factor and qty_base (lines matched on source_*).

Usage (the run folder before --shops):
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_7.py runs/explore/v2/9B/public_test/step_9_conversion
  .venv/Scripts/python.exe -I scripts/write_code/run_stage_7.py <run folder> --shops dev_005 --out-dir runs/write_code/x
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_stages_1_4 import Tee, plan_of  # noqa: E402  (first: it puts v2_runner on sys.path)
from run_stage_5 import KEYS, same  # noqa: E402
from run_stage_6 import keyed  # noqa: E402

import pandas as pd  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.write_code import (apply_rules, apply_stages_1_4, apply_units,  # noqa: E402
                                                                     normalize)

COMPARED = {"unit": "label", "base_unit": "label", "unit_factor": "qty", "qty_base": "qty"}


def compare(lines: pd.DataFrame, ref_path: Path) -> None:
    ref = keyed(pd.read_parquet(ref_path))
    sqlite = ref["source_file"].str.lower().str.endswith(".sqlite")
    ref.loc[sqlite, "source_row"] = ref.loc[sqlite, "source_row"] + 1  # see run_stage_5.compare
    ref = ref.drop_duplicates(KEYS)  # dev_002: topping lines share their drink's source row
    m = keyed(lines).merge(ref[KEYS + list(COMPARED)], on=KEYS, how="inner", suffixes=("", "_ref"))
    print(f"  vs reference: {len(m):,} lines matched")
    for c, kind in COMPARED.items():
        a, b = m[c], m[c + "_ref"]
        if kind == "qty":
            a = pd.to_numeric(a, errors="coerce")
            ok = ((a - pd.to_numeric(b, errors="coerce")).abs() < 1e-4) | (a.isna() & b.isna())
        else:
            ok = same(kind, a, b)
        bad = m[~ok]
        line = f"    {c:<12} {ok.mean():>8.3%} agree"
        if len(bad):
            ex = [f"{r.source_file} [{r.source_sheet}] row {r.source_row}: ours {getattr(r, c)!r} ref "
                  f"{getattr(r, c + '_ref')!r}" for r in bad.head(3).itertuples()]
            line += f" ({len(bad):,} differ) e.g.\n        " + "\n        ".join(ex)
        print(line)


def print_report(r) -> None:
    for s in r.sources:
        print(f"    source {s}")
    for s in r.source_failures[:5]:
        print(f"    SOURCE FAILURE {s}")
    if len(r.source_failures) > 5:
        print(f"    ... {len(r.source_failures) - 5} more source failures")
    for s in r.base_conflicts:
        print(f"    base unit conflict {s}")
    print("    cases: " + ", ".join(f"{k} {n:,}" for k, n in r.cases.items()))
    if r.base_from_sources or r.base_from_unit:
        print(f"    base_unit from a conversion source on {r.base_from_sources:,} lines, the line's own unit on "
              f"{r.base_from_unit:,}")
    if r.same_code_lines or r.same_code_ambiguous:
        print(f"    same code, same product: {r.same_code_lines:,} lines matched through their code"
              + (f"; {r.same_code_ambiguous:,} left (code matched several source products)"
                 if r.same_code_ambiguous else ""))
    if r.no_qty_column:
        print("    no qty column: unit_factor left empty")
    if r.cross_check:
        print(f"    cross-check: {r.cross_check:,} lines whose qty_base / qty differs from the source, e.g. "
              f"{r.cross_check_examples[:3]}")
    if r.no_unit_for_factor:
        n = sum(r.no_unit_for_factor.values())
        print(f"    NO UNIT for the factor on {n:,} lines, e.g. {r.no_unit_for_factor.most_common(3)}")
    if r.failures:
        n = sum(r.failures.values())
        print(f"    NO FACTOR on {n:,} lines ({len(r.failures)} product x unit), e.g.")
        for (case, p, u, why), k in r.failures.most_common(5):
            print(f"        {k:>6,} x case {case} {p!r} {u!r}: {why}")


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
    except Exception as e:  # noqa: BLE001 - one shop's failure must not stop the others
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return
    print(f"  {len(units.lines):,} lines in {time.perf_counter() - start:.1f}s; conversions in the plan: "
          f"{[c.kind for c in plan.conversions] or 'none'}")
    print_report(units.report)
    ref = v2_runner.ROOT / "data_std" / split / shop / "sales_lines.parquet"
    if ref.exists():
        compare(units.lines, ref)


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
        print(f"stages 1-7 on {', '.join(args.paths)}")
    try:
        for split, shop, state in jobs:
            report(split, shop, state)
    finally:
        if out:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            out.close()


if __name__ == "__main__":
    main()
