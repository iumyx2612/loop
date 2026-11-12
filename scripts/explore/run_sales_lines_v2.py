"""Find each shop's sales-line tables: the explore_agent v2 up to step 1 (confirm_headers → prepare →
choose_line_tables). Loads .env (API key / endpoint); the user runs it.

Writes runs/explore/<timestamp>-v2-lines/<shop>.json (format in v2_runner.py): the line tables chosen, the trace
and the whole state, which run_column_mapping_v2.py loads to run step 2 without redoing step 1.

Usage:
  .venv/Scripts/python.exe scripts/explore/run_sales_lines_v2.py --model <model> biz_108 biz_116 biz_117 biz_122
  .venv/Scripts/python.exe scripts/explore/run_sales_lines_v2.py --model <model> --split public_test dev_001 dev_004
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import sales_lines_graph  # noqa: E402

load_dotenv()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("shops", nargs="+")
    p.add_argument("--split", default="train")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    out_dir = v2_runner.out_dir_for(args, "-v2-lines")
    v2_runner.write_run(out_dir, {"graph": "sales_lines_graph", "model": args.model, "split": args.split,
                                  "shops": args.shops, "max_tokens": args.max_tokens, "temperature": args.temperature})
    jobs = [(args.split, s, v2_runner.new_state(args.split, s)) for s in args.shops]
    v2_runner.run_all(sales_lines_graph, "sales_lines_graph", jobs, v2_runner.make_model(args), args.workers, out_dir)


if __name__ == "__main__":
    main()
