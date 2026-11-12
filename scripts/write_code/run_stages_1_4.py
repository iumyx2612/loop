"""Run write_code's fixed stages 1-4 (read, forward fill, joins, select) on saved explore runs. No LLM and no .env.

Takes the plan from each saved state (state["plan"], written by write_plan). Plans saved before a plan field
existed are patched from the state: header_rows (state["header_rows"]), conversions (state["conversions"], or []).
Prints per shop: rows read / dropped, each join's match rate, each schema column's fill rate with example values,
and the reference conversion's row count when data_std/<split>/<shop>/sales_lines.parquet exists (it is after
stage 6's drops, so it may be a little lower).

Usage (the run folder before --shops):
  .venv/Scripts/python.exe -I scripts/write_code/run_stages_1_4.py runs/explore/v2/9B/train/step_9_conversion
  .venv/Scripts/python.exe -I scripts/write_code/run_stages_1_4.py <run folder> --shops biz_108 biz_122
  .venv/Scripts/python.exe -I scripts/write_code/run_stages_1_4.py <run folder> --out-dir runs/write_code/<name>
    (--out-dir also writes the report to <out-dir>/_summary.txt)
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "explore"))  # v2_runner (puts ROOT on sys.path, utf-8 stdout)

import pyarrow.parquet as pq  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.write_code import apply_stages_1_4  # noqa: E402
from src.datamate.parse import is_blank  # noqa: E402


def plan_of(state: dict) -> SalesLinePlan | None:
    data = state.get("plan")
    if not data:
        return None
    data = dict(data)
    if not isinstance(data.get("conversions"), list):  # a plan saved before the conversions step
        data["conversions"] = [c.model_dump() if hasattr(c, "model_dump") else c
                               for c in state.get("conversions") or []]
    data.pop("conversion_table", None)
    if not data.get("header_rows"):
        data["header_rows"] = state.get("header_rows") or {}
    return SalesLinePlan(**data)


def short(v) -> str:
    s = repr(v)
    return s if len(s) <= 40 else s[:39] + "…"


def report(split: str, shop: str, state: dict) -> None:
    print(f"\n## {shop}")
    plan = plan_of(state)
    if plan is None:
        print("  no plan in the saved state")
        return
    start = time.perf_counter()
    try:
        a = apply_stages_1_4(Path(state["shop_dir"]), plan)
    except Exception as e:  # noqa: BLE001 - one shop's failure must not stop the others
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=3)
        return
    r = a.read
    print(f"  {len(a.lines):,} lines in {time.perf_counter() - start:.1f}s; dropped: skipped_rows {r.skipped}, "
          f"blank {r.blank}, subtotal regex {r.subtotal}")
    ref = v2_runner.ROOT / "data_std" / split / shop / "sales_lines.parquet"
    if ref.exists():
        print(f"  reference: {pq.ParquetFile(ref).metadata.num_rows:,} lines")
    for t in r.tables:
        print(f"    read {t}")
    for x in r.renamed:
        print(f"    renamed {x}")
    for n in a.notes:
        print(f"  stage 2: {n}")
    for j in a.joined.report:
        pct = j["lines_matched"] / j["lines_with_key"] if j["lines_with_key"] else 0
        print(f"  join {j['join']}: {j['lines_matched']:,}/{j['lines_with_key']:,} lines matched ({pct:.0%}), "
              f"{j['rows']:,} rows, repeated keys {j['repeated_keys']}"
              + (f"; unmatched e.g. {j['unmatched_examples']}" if j["unmatched_examples"] else ""))
    for col in [c for c in a.lines.columns if not c.startswith("source_")]:
        s = a.lines[col]
        filled = s.map(lambda v: not is_blank(v))
        examples = list(dict.fromkeys(short(v) for v in s[filled]))[:3]
        print(f"    {col:<15} {filled.mean():>5.0%} filled  e.g. {', '.join(examples)}")


class Tee:
    """Print to the console and to a file at once (the report is written as it is printed)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


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
        sys.stderr = Tee(sys.__stderr__, out)  # tracebacks of failed shops too
        print(f"stages 1-4 on {', '.join(args.paths)}")
    try:
        for split, shop, state in jobs:
            report(split, shop, state)
    finally:
        if out:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            out.close()


if __name__ == "__main__":
    main()
