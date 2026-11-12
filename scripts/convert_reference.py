"""Run the hand-written reference converters: raw shop files -> data_std/<split>/<shop>/sales_lines.parquet + manifest.json.

Usage: .venv/Scripts/python.exe -I scripts/convert_reference.py [--split train|public_test] [shop ...]
       (default: every shop of the split with a converter)
"""
import argparse
import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate import schema  # noqa: E402

SHOPS = {
    "train": ["biz_108", "biz_116", "biz_117", "biz_122", "biz_128"],
    "public_test": [f"dev_{i:03d}" for i in range(1, 15)],
}


def main(split: str, shops: list[str]) -> None:
    for shop in shops:
        t0 = time.time()
        module = importlib.import_module(f"src.datamate.reference.{shop}")
        result = module.convert(ROOT / "dataset" / "zaloAI" / split / "data" / shop)
        df = schema.finalize(result.lines)
        problems = schema.validate(df)
        m = schema.write(df, ROOT / "data_std" / split / shop, shop, result.sources, result.data_end, result.brief)
        print(f"## {shop}  {m['rows']} lines, {m['orders']} orders, {m['date_min']} .. {m['date_max']} "
              f"(data end {m['data_end']}), {time.time() - t0:.0f}s")
        print(f"   statuses: {m['status_counts']}")
        print(f"   missing columns: {', '.join(m['columns_missing']) or '-'}")
        for note in result.notes:
            print(f"   note: {note}")
        for p in problems:
            print(f"   PROBLEM: {p}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="train", choices=sorted(SHOPS))
    p.add_argument("shops", nargs="*")
    args = p.parse_args()
    main(args.split, args.shops or SHOPS[args.split])
