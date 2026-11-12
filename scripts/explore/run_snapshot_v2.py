"""Run step 3's snapshot (one LLM call per attached table, asked only the fields its anchors look up) on saved
states. Loads .env (API key / endpoint); the user runs it.

Loads shop files that already went through attach (run_attach_v2.py) or attach + resolve_anchors
(run_resolve_anchors_v2.py, the normal input), runs snapshot_graph on each state and writes
runs/explore/<timestamp>-v2-snapshot/<shop>.json (format in v2_runner.py) + _summary.txt. Earlier steps are not
redone.

Usage (a path is a run folder, i.e. all its shop files, or one shop file; the folder before --shops):
  .venv/Scripts/python.exe scripts/explore/run_snapshot_v2.py runs/explore/<...>-v2-resolve --model <model> --max-tokens 4000
  .venv/Scripts/python.exe scripts/explore/run_snapshot_v2.py runs/explore/<...>-v2-resolve --model <model> --max-tokens 4000 --shops biz_108
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import snapshot_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after resolve_anchors)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    missing = [shop for _, shop, state in jobs if "attached" not in state]
    if missing:
        raise SystemExit(f"no attach result in the saved state of: {', '.join(missing)}")
    out_dir = v2_runner.out_dir_for(args, "-v2-snapshot")
    v2_runner.write_run(out_dir, {"graph": "snapshot_graph", "model": args.model, "from": args.paths,
                                  "shops": [s for _, s, _ in jobs], "max_tokens": args.max_tokens,
                                  "temperature": args.temperature})
    v2_runner.run_all(snapshot_graph, "snapshot_graph", jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
