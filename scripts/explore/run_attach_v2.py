"""Run step 3's attach only (plain code: which tables link to the sales lines, and through which column) on saved
states. No LLM and no .env: anyone can run it.

Loads shop files that already went through find_anchors (run_find_anchors_v2.py's output), runs attach_graph on
each state and writes runs/explore/<timestamp>-v2-attach/<shop>.json (format in v2_runner.py) + _summary.txt.

Usage (a path is a run folder, i.e. all its shop files, or one shop file):
  .venv/Scripts/python.exe -I scripts/explore/run_attach_v2.py runs/explore/v2/train/step_3a_find_anchors
  .venv/Scripts/python.exe -I scripts/explore/run_attach_v2.py runs/explore/v2/train/step_3a_find_anchors --shops biz_108 biz_123
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import attach_graph  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after find_anchors)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "anchor_tables" not in state]
    if missing:
        raise SystemExit(f"no find_anchors result in the saved state of: {', '.join(missing)}")
    out_dir = v2_runner.out_dir_for(args, "-v2-attach")
    v2_runner.write_run(out_dir, {"graph": "attach_graph", "from": args.paths, "shops": [s for _, s, _ in jobs]})
    v2_runner.run_all(attach_graph, "attach_graph", jobs, None, args.workers, out_dir)  # no model: no LLM step


if __name__ == "__main__":
    main()
