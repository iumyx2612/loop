"""Print the console summary of a finished v2 run again, from its saved shop files (no LLM, no .env).

Usage (a path is a run folder, i.e. all its shop files, or one shop file):
  .venv/Scripts/python.exe -I scripts/explore/show_run_v2.py runs/explore/v2/train/step_3_trial_fill_missing
  .venv/Scripts/python.exe -I scripts/explore/show_run_v2.py runs/explore/v2/train/step_3_trial_fill_missing/biz_108.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # v2_runner (not on sys.path under -I)

import v2_runner  # noqa: E402


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    files = []
    for p in map(Path, sys.argv[1:]):
        files += sorted(f for f in p.glob("*.json") if not f.name.startswith("_")) if p.is_dir() else [p]
    print("\n\n".join(v2_runner.summary_from_file(f) for f in files))


if __name__ == "__main__":
    main()
