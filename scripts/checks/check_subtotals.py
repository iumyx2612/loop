"""Check converted sales lines against the subtotal rows the raw exports contain (`TỔNG NGÀY dd/mm`, `tổng d/m`,
`Tong cong` ...). The converters drop those rows; here they are re-read from the raw files and compared with the
converted lines: a subtotal row closes the day (or sheet) of the data row just above it.

For each shop, prints how many subtotals match the converted line amounts summed over (a) all lines, (b) orders not
cancelled and not returned, (c) orders not cancelled, (d) completed only. A shop whose subtotals match one rule
everywhere has its amounts, dates and duplicate removal right; the misses are listed.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_subtotals.py [--split public_test] [shop ...]
"""
import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402

from src.datamate.parse import parse_money  # noqa: E402
from src.datamate.reference.common import SUBTOTAL  # noqa: E402

RULES = {
    "all lines": lambda s: s.notna(),
    "not cancelled/returned": lambda s: s.isin(["completed", "in_delivery"]),
    "not cancelled": lambda s: s != "cancelled",
    "completed": lambda s: s == "completed",
}


def raw_rows(path: Path):
    """(sheet, row number, cells) for every row of an xlsx / csv file; row numbers as in read_xlsx / read_csv."""
    if path.suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for i, r in enumerate(csv.reader(fh), start=1):
                yield "", i, r
        return
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sn in wb.sheetnames:
        for i, r in enumerate(wb[sn].iter_rows(values_only=True), start=1):
            yield sn, i, r
    wb.close()


def amounts(cells) -> list[float]:
    out = []
    for v in cells:
        if v is None or v == "" or (isinstance(v, str) and SUBTOTAL.match(v)):
            continue
        try:
            m = parse_money(v)
        except ValueError:
            continue
        if m is not None and m != 0:
            out.append(m)
    return out


def check_shop(split: str, shop: str) -> None:
    lines = pd.read_parquet(ROOT / "data_std" / split / shop / "sales_lines.parquet")
    shop_dir = ROOT / "dataset" / "zaloAI" / split / "data" / shop
    files = sorted(f for f in set(lines["source_file"]) if Path(f).suffix in (".xlsx", ".csv"))
    found = []
    for f in files:
        file_lines = lines[lines["source_file"] == f]
        for sheet, row, cells in raw_rows(shop_dir / f):
            label = next((v for v in cells if isinstance(v, str) and SUBTOTAL.match(v)), None)
            if label is None or label.strip().lower().startswith("ghi chú"):
                continue
            here = file_lines[(file_lines["source_sheet"] == sheet) & (file_lines["source_row"] < row)]
            if here.empty:
                continue
            last = here.loc[here["source_row"].idxmax()]
            low = label.strip().lower()
            monthly = "tháng" in low or low in ("tổng cộng", "tong cong", "cộng")
            if monthly:  # the sheet's lines, or the month's lines when months share one sheet
                month = here["order_date"].dt.to_period("M") == last["order_date"].to_period("M")
                scope = here[month] if "tháng" in low else here
            else:
                scope = lines[lines["order_date"] == last["order_date"]]
            if not amounts(cells):  # a repeated header whose column title looks like a label ("Cộng")
                continue
            found.append({"file": f, "sheet": sheet, "row": row, "label": label.strip(), "day": last["order_date"].date(),
                          "monthly": monthly, "amounts": amounts(cells), "scope": scope})
    if not found:
        print(f"## {shop}: no subtotal rows")
        return
    print(f"## {shop}: {len(found)} subtotal rows")
    for rule, sel in RULES.items():
        miss = [x for x in found if round(x["scope"].loc[sel(x["scope"]["status"]), "line_amount"].sum()) not in
                {round(a) for a in x["amounts"]}]
        print(f"   {rule:24} match {len(found) - len(miss):4}/{len(found)}")
        if len(miss) < len(found) and miss:
            for x in miss[:5]:
                got = x["scope"].loc[sel(x["scope"]["status"]), "line_amount"].sum()
                print(f"      miss {x['file']} [{x['sheet']}] row {x['row']} {x['label']!r} ({x['day']}): "
                      f"subtotal {x['amounts']} vs converted {got:,.0f}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--split", default="public_test")
    p.add_argument("shops", nargs="*")
    args = p.parse_args()
    shops = args.shops or sorted(d.name for d in (ROOT / "data_std" / args.split).iterdir())
    for s in shops:
        check_shop(args.split, s)
