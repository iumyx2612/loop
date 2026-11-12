"""Run step 5 (map_statuses: the shop's status words → the four statuses) on saved states. Loads .env (API key /
endpoint); the user runs it.

Loads shop files that went through steps 2-3 (a full run, or a snapshot / report_missing run) and writes
runs/explore/<timestamp>-v2-statuses/<shop>.json (format in v2_runner.py) + _summary.txt: per shop the status column,
each word with its count, its status and where it came from (known list / LLM); the LLM's answers compared with the
answer key parse.map_status (✓ / ✗ / ? unknown word). --no-known-words: every word goes to the LLM (on train /
public test the list knows every word, so this is the only way to measure the LLM).
A state saved before the filter step (no skipped_rows) runs filter_rows first (filter_statuses_graph), so the words
come from data rows only.

Usage (a path is a run folder, i.e. all its shop files, or one shop file):
  .venv/Scripts/python.exe scripts/explore/run_statuses_v2.py runs/explore/<full run> --model <model> --max-tokens 4000
  .venv/Scripts/python.exe scripts/explore/run_statuses_v2.py runs/explore/<full run> --shops biz_122 dev_011 --model <model>
  .venv/Scripts/python.exe scripts/explore/run_statuses_v2.py runs/explore/<full run> --no-known-words --model <model>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import filter_statuses_graph, statuses_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+", help="run folders or shop files with a saved state (after steps 2-3)")
    p.add_argument("--shops", nargs="*", default=None, help="only these shops of the loaded files")
    p.add_argument("--no-known-words", action="store_true",
                   help="ask the LLM every word (ignore the known-word list), to measure the LLM alone")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    jobs = v2_runner.load_saved(args.paths, args.shops)
    if not jobs:
        raise SystemExit("no shop files found")
    for _, _, state in jobs:
        state["known_status_words"] = not args.no_known_words
    missing = [shop for _, shop, state in jobs if "columns" not in state]
    if missing:
        raise SystemExit(f"no step 2 result in the saved state of: {', '.join(missing)}")
    old = any("skipped_rows" not in state for _, _, state in jobs)
    graph, name = (filter_statuses_graph, "filter_statuses_graph") if old else (statuses_graph, "statuses_graph")
    out_dir = v2_runner.out_dir_for(args, "-v2-statuses")
    v2_runner.write_run(out_dir, {"graph": name, "from": args.paths, "model": args.model,
                                  "known_status_words": not args.no_known_words,
                                  "shops": [s for _, s, _ in jobs], "max_tokens": args.max_tokens,
                                  "temperature": args.temperature})
    v2_runner.run_all(graph, name, jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
