"""Run step 3's find_anchors only (one call per non-line table: which columns are anchors) on saved states.
Loads .env (API key / endpoint); the user runs it.

Loads shop files that already went through step 2 (run_column_mapping_v2.py's output), runs find_anchors_graph on
each state and writes runs/explore/<timestamp>-v2-anchors/<shop>.json (format in v2_runner.py). Steps 1-2 are not
redone. The console summary shows each table's anchors, then per anchor every table that claimed it.

Usage (a path is a run folder, i.e. all its shop files, or one shop file):
  .venv/Scripts/python.exe scripts/explore/run_find_anchors_v2.py --model <model> --max-tokens 4000 runs/explore/v2/train/step_2_column_mapping
  .venv/Scripts/python.exe scripts/explore/run_find_anchors_v2.py --model <model> runs/explore/v2/train/step_2_column_mapping --shops biz_108 biz_116
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import find_anchors_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after step 2)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "columns" not in state]
    if missing:
        raise SystemExit(f"no step-2 result in the saved state of: {', '.join(missing)}")
    out_dir = v2_runner.out_dir_for(args, "-v2-anchors")
    v2_runner.write_run(out_dir, {"graph": "find_anchors_graph", "model": args.model, "from": args.paths,
                                  "shops": [shop for _, shop, _ in jobs], "max_tokens": args.max_tokens,
                                  "temperature": args.temperature})
    v2_runner.run_all(find_anchors_graph, "find_anchors_graph", jobs, v2_runner.make_model(args),
                      args.workers, out_dir)


if __name__ == "__main__":
    main()
