"""For every table of every shop: header row found from the README's column list vs the guess.

Reports README headings that match no file, tables the README describes but whose header row was not found,
disagreements between the README row and the guess, README columns missing from the header, and tables with
no README column list (the LLM fallback's workload).

Usage: .venv/Scripts/python.exe -I scripts/checks/check_readme_headers.py [shop ...]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.analyst_agent.explore_agent import explore  # noqa: E402
from src.datamate.analyst_agent.explore_agent.readme import readme_columns, readme_tables  # noqa: E402

DATA = ROOT / "dataset" / "zaloAI"


def main(only: list[str]) -> None:
    shop_dirs = [d for split in ("train", "public_test") for d in sorted((DATA / split / "data").iterdir())
                 if d.is_dir() and (not only or d.name in only)]
    totals = {"readme": 0, "guess": 0, "disagree": 0, "unmatched": 0}
    for shop_dir in shop_dirs:
        files = explore._data_files(shop_dir)
        notes = []
        for spec in readme_tables(shop_dir):
            if not any(spec.matches(f.name, s) for f in files for s in explore._sheets(shop_dir, f.name)):
                notes.append(f"README heading matches no table: {spec.file_pattern} sheet={spec.sheet}")
        for f in files:
            for t in explore._all_tables(shop_dir, f.name):
                guess = explore._guess_header(t.rows, f.suffix == ".xlsx")
                label = explore._label(t)
                totals[t.header_source] += 1
                if t.header_source == "readme":
                    if guess != t.header_row:
                        totals["disagree"] += 1
                        notes.append(f"{label}: README row {t.header_row}, guess row {guess}")
                    if t.readme_missing:
                        notes.append(f"{label}: README columns not in header: {', '.join(t.readme_missing)}")
                elif readme_columns(shop_dir, f.name, t.sheet):
                    totals["unmatched"] += 1
                    notes.append(f"{label}: README lists columns but no header row holds half of them")
                else:
                    notes.append(f"{label}: no README columns, guess row {guess}")
        print(f"## {shop_dir.parent.parent.name}/{shop_dir.name}")
        for n in notes:
            print(f"   {n}")
    print(f"\ntables: header from README {totals['readme']} (guess disagrees on {totals['disagree']}), "
          f"guessed {totals['guess']} (README lists columns but not found: {totals['unmatched']})")


if __name__ == "__main__":
    main(sys.argv[1:])
