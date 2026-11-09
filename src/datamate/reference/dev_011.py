"""dev_011 (phone shop, owner's book): one sheet for the whole period; STT and the order fields only on an order's
first line. Shared logic in phone_shop.py."""
from pathlib import Path

from src.datamate.reference.common import Converted, drop_duplicate_lines, drop_subtotals, read_xlsx, readme_data_end
from src.datamate.reference.phone_shop import BRIEF, convert_book

BOOK = "SoBanHang.xlsx"


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / BOOK, sheets=["Sổ bán hàng"])
    raw = drop_subtotals(raw, notes)
    raw["order_no"] = raw["STT"].ffill()  # a blank STT continues the order above (§8)
    raw = drop_duplicate_lines(raw, "order_no", notes)
    df = convert_book(raw, read_xlsx(shop_dir / BOOK, sheets=["Ghi tắt"]), notes)
    return Converted(df, [f"{BOOK} [Sổ bán hàng, Ghi tắt]"], readme_data_end(shop_dir / "README.md"), notes, BRIEF)
