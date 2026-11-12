"""Run the whole explore_agent v2 from scratch (every step: confirm_headers → prepare → filter_rows →
choose_line_tables → map_line_columns → find_anchors → attach → resolve_anchors → snapshot → report_missing →
map_statuses → map_line_kinds → cleaning_inputs → write_plan). Loads .env (API key / endpoint); the user runs it.

Writes runs/explore/<timestamp>-v2-full/<shop>.json (format in v2_runner.py) + _summary.txt: the trace of every
step and the whole state, so any partial runner (run_snapshot_v2.py, ...) can later rerun one step on it.

Usage:
  .venv/Scripts/python.exe scripts/explore/run_explore_v2.py --model <model> --max-tokens 4000 biz_108 biz_116
  .venv/Scripts/python.exe scripts/explore/run_explore_v2.py --model <model> --max-tokens 4000 --split public_test --all
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import explore_agent  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("shops", nargs="*")
    p.add_argument("--split", default="train")
    p.add_argument("--all", action="store_true", help="every shop of the split")
    p.add_argument("--no-known-words", action="store_true",
                   help="step 5: ask the LLM every status word (ignore the known-word list)")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    shops = sorted(d.name for d in (v2_runner.DATA / args.split / "data").iterdir() if d.is_dir()) \
        if args.all else args.shops
    if not shops:
        raise SystemExit("name some shops, or pass --all")
    out_dir = v2_runner.out_dir_for(args, "-v2-full")
    v2_runner.write_run(out_dir, {"graph": "explore_agent", "model": args.model, "split": args.split,
                                  "shops": shops, "max_tokens": args.max_tokens, "temperature": args.temperature})
    jobs = [(args.split, s, {**v2_runner.new_state(args.split, s), "known_status_words": not args.no_known_words})
            for s in shops]
    v2_runner.run_all(explore_agent, "explore_agent", jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
