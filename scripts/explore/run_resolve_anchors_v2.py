"""Run step 3's attach + resolve_anchors (which tables link to the lines; one LLM call per anchor several tables
link by, to pick the shop's own list) on saved states. Loads .env (API key / endpoint); the user runs it.

Loads shop files that already went through find_anchors (run_find_anchors_v2.py's output), runs anchors_graph on
each state and writes runs/explore/<timestamp>-v2-resolve/<shop>.json (format in v2_runner.py) + _summary.txt.
find_anchors is not redone. --min-unique 0.98 lowers attach's uniqueness rule (default 0.99) so the payments
tables attach beside the orders tables: the wrong-table case the 10 shops do not have otherwise.

Usage (a path is a run folder, i.e. all its shop files, or one shop file; the folder before --shops):
  .venv/Scripts/python.exe scripts/explore/run_resolve_anchors_v2.py runs/explore/v2/train/step_3a_find_anchors --model <model> --max-tokens 4000
  .venv/Scripts/python.exe scripts/explore/run_resolve_anchors_v2.py runs/explore/v2/train/step_3a_find_anchors --model <model> --max-tokens 4000 --min-unique 0.98
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import anchors_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after find_anchors)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    p.add_argument("--min-unique", type=float, default=None,
                   help="attach's uniqueness rule for this run (default 0.99); 0.98 attaches the payments tables")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "anchor_tables" not in state]
    if missing:
        raise SystemExit(f"no find_anchors result in the saved state of: {', '.join(missing)}")
    if args.min_unique is not None:
        for _, _, state in jobs:
            state["min_unique"] = args.min_unique
    out_dir = v2_runner.out_dir_for(args, "-v2-resolve")
    v2_runner.write_run(out_dir, {"graph": "anchors_graph", "model": args.model, "from": args.paths,
                                  "shops": [s for _, s, _ in jobs], "min_unique": args.min_unique,
                                  "max_tokens": args.max_tokens, "temperature": args.temperature})
    v2_runner.run_all(anchors_graph, "anchors_graph", jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
