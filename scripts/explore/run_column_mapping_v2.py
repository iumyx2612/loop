"""Run step 2 only (map_line_columns) on saved states. Loads .env (API key / endpoint); the user runs it.

Loads the shop files written by run_sales_lines_v2.py (or by an earlier run of this script), runs
column_mapping_graph on each state and writes runs/explore/<timestamp>-v2-columns/<shop>.json (format in
v2_runner.py): the column mapping, the trace (earlier steps' records kept) and the whole state. Step 1 is not
redone, so the same line tables can be mapped again and again while the step-2 prompt changes.

Usage (a path is a run folder, i.e. all its shop files, or one shop file):
  .venv/Scripts/python.exe scripts/explore/run_column_mapping_v2.py --model <model> runs/explore/<timestamp>-v2-lines
  .venv/Scripts/python.exe scripts/explore/run_column_mapping_v2.py --model <model> runs/explore/<timestamp>-v2-lines --shops biz_108 biz_117
  .venv/Scripts/python.exe scripts/explore/run_column_mapping_v2.py --model <model> runs/explore/<t>-v2-lines/biz_122.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import column_mapping_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    out_dir = v2_runner.out_dir_for(args, "-v2-columns")
    v2_runner.write_run(out_dir, {"graph": "column_mapping_graph", "model": args.model, "from": args.paths,
                                  "shops": [shop for _, shop, _ in jobs], "max_tokens": args.max_tokens, "temperature": args.temperature})
    v2_runner.run_all(column_mapping_graph, "column_mapping_graph", jobs, v2_runner.make_model(args),
                      args.workers, out_dir)


if __name__ == "__main__":
    main()
