"""dev_014 (phone shop, owner's books): sales book with one sheet per month (a blank row above the header) and the
`Ghi tắt` sheet; STT and the order fields repeated on every line of an order; footer rows. Shared logic in
phone_shop.py. The trade-in, debt, instalment and warranty books are not sales lines."""
from pathlib import Path

import openpyxl

from src.datamate.reference.common import Converted, drop_duplicate_lines, drop_subtotals, read_xlsx, readme_data_end
from src.datamate.reference.phone_shop import BRIEF, convert_book

BOOK = "SoBanHang.xlsx"


def convert(shop_dir: Path) -> Converted:
    notes = []
    wb = openpyxl.load_workbook(shop_dir / BOOK, read_only=True)
    months = [s for s in wb.sheetnames if s != "Ghi tắt"]
    wb.close()
    raw = read_xlsx(shop_dir / BOOK, header_row=2, sheets=months)
    raw = drop_subtotals(raw, notes)
    raw["order_no"] = raw["STT"]
    raw = drop_duplicate_lines(raw, "order_no", notes)
    df = convert_book(raw, read_xlsx(shop_dir / BOOK, sheets=["Ghi tắt"]), notes)
    return Converted(df, [f"{BOOK} [month sheets, Ghi tắt]"], readme_data_end(shop_dir / "README.md"), notes, BRIEF)
