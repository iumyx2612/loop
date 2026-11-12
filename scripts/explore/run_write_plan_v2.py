"""Run step 8's write_plan only (plain code: put the SalesLinePlan together, list unmapped columns, plan_check) on
saved states. No LLM and no .env: anyone can run it.

Loads shop files that went through cleaning_inputs (any state after step 2 works; fields of steps not run are
empty in the plan). Runs write_plan_graph on each state and writes runs/explore/<timestamp>-v2-plan/<shop>.json
(format in v2_runner.py; the plan is state["plan"]) + _summary.txt.

Usage (a path is a run folder, i.e. all its shop files, or one shop file; the folder before --shops):
  .venv/Scripts/python.exe -I scripts/explore/run_write_plan_v2.py runs/explore/<run folder>
  .venv/Scripts/python.exe -I scripts/explore/run_write_plan_v2.py runs/explore/<run folder> --shops dev_013
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import write_plan_graph  # noqa: E402


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
    out_dir = v2_runner.out_dir_for(args, "-v2-plan")
    v2_runner.write_run(out_dir, {"graph": "write_plan_graph", "from": args.paths,
                                  "shops": [s for _, s, _ in jobs]})
    v2_runner.run_all(write_plan_graph, "write_plan_graph", jobs, None, args.workers, out_dir)  # no LLM


if __name__ == "__main__":
    main()
