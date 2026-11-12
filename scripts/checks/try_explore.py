"""Call one explore_agent tool by hand and print what the LLM would read.

Usage: .venv/Scripts/python.exe -I scripts/checks/try_explore.py <split> <shop> <tool> [key=value ...]
  e.g. ... train biz_117 list_files
       ... train biz_117 peek file=SoBanHang.xlsx sheet=T1 start=1 n=8
       ... train biz_117 column_values file=SoBanHang.xlsx column="Trạng thái"
       ... train biz_117 find_rows file=SoBanHang.xlsx text="tổng tháng"
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.analyst_agent.explore_agent import explore  # noqa: E402

TOOLS = {"read_readme": explore.read_readme, "list_files": explore.list_files, "peek": explore.peek,
         "column_values": explore.column_values, "find_rows": explore.find_rows}
INT_ARGS = {"start", "n"}


def main() -> None:
    split, shop, tool, *pairs = sys.argv[1:]
    kwargs = {}
    for pair in pairs:
        k, v = pair.split("=", 1)
        kwargs[k] = int(v) if k in INT_ARGS else v
    if tool in ("column_values", "find_rows"):
        kwargs.setdefault("sheet", None)
    shop_dir = ROOT / "dataset" / "zaloAI" / split / "data" / shop
    t0 = time.time()
    try:
        out = TOOLS[tool](shop_dir, **kwargs)
    except explore.ExploreError as e:
        out = f"ERROR: {e}"
    print(out)
    print(f"\n[{len(out)} chars, {time.time() - t0:.1f}s]")


if __name__ == "__main__":
    main()
