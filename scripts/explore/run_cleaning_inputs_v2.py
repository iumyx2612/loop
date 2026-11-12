"""Run step 6's cleaning_inputs only (plain code: return lines, forward fill, duplicates) on saved states. No LLM and
no .env: anyone can run it.

Loads shop files that already went through step 2 (any later run works too). Give it run_line_kinds_v2.py's output
so 6c leaves step 7's not-revenue rows out; on an earlier state (no non_revenue_names) 6c counts every row.
Runs cleaning_inputs_graph on each state and writes runs/explore/<timestamp>-v2-cleaning/<shop>.json (format in
v2_runner.py) + _summary.txt.

Usage (a path is a run folder, i.e. all its shop files, or one shop file; the folder before --shops):
  .venv/Scripts/python.exe -I scripts/explore/run_cleaning_inputs_v2.py runs/explore/v2/train/step_5_status_words
  .venv/Scripts/python.exe -I scripts/explore/run_cleaning_inputs_v2.py runs/explore/v2/train/step_5_status_words --shops biz_122
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import cleaning_inputs_graph  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after step 2)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "columns" not in state]
    if missing:
        raise SystemExit(f"no step 2 result in the saved state of: {', '.join(missing)}")
    out_dir = v2_runner.out_dir_for(args, "-v2-cleaning")
    v2_runner.write_run(out_dir, {"graph": "cleaning_inputs_graph", "from": args.paths,
                                  "shops": [s for _, s, _ in jobs]})
    v2_runner.run_all(cleaning_inputs_graph, "cleaning_inputs_graph", jobs, None, args.workers, out_dir)  # no LLM


if __name__ == "__main__":
    main()
