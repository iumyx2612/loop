"""Run the conversions step (map_conversions: where the shop writes its unit conversions) on saved states. Loads .env
(API key / endpoint, for the README fallback call); the user runs it.

Parts 1-4 (README list, README fallback + rows, which table, its columns; v2/conversions/nodes.py). Loads any
shop file saved after prepare (in the full graph the step runs after cleaning_inputs, before write_plan) and writes
runs/explore/<timestamp>-v2-conversions/<shop>.json (format in v2_runner.py) + _summary.txt.

Usage (a path is a run folder, i.e. all its shop files, or one shop file; the folder before --shops):
  .venv/Scripts/python.exe -I scripts/explore/run_conversions_v2.py runs/explore/<run folder> --model <model> --max-tokens 4000
  .venv/Scripts/python.exe -I scripts/explore/run_conversions_v2.py runs/explore/<run folder> --shops dev_004 --model <model>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import conversions_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after prepare)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "readme" not in state]
    if missing:
        raise SystemExit(f"no README in the saved state of (run prepare first): {', '.join(missing)}")
    out_dir = v2_runner.out_dir_for(args, "-v2-conversions")
    v2_runner.write_run(out_dir, {"graph": "conversions_graph", "from": args.paths, "model": args.model,
                                  "shops": [s for _, s, _ in jobs], "max_tokens": args.max_tokens,
                                  "temperature": args.temperature})
    v2_runner.run_all(conversions_graph, "conversions_graph", jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
