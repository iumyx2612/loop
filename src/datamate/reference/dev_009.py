"""dev_009 (order list only, CSV): one row per order, no line items. Each order becomes one line whose amount is
"Giá trị hàng" (item total before the order discount, without shipping: revenue as rulebook §1 defines it).
Product columns stay empty, so the manifest reports them as not recorded (§7: product questions are unanswerable)."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, customer_name_key, map_status, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, money, read_csv, readme_data_end,
)


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_csv(shop_dir / "danh_sach_hoa_don.csv")
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã hóa đơn", notes)
    dup = raw["Mã hóa đơn"].map(clean_text).duplicated(keep=False)
    if dup.any():
        raise ValueError(f"{int(dup.sum())} rows share an order id with a different row: not one row per order")

    df = pd.DataFrame({
        "order_id": raw["Mã hóa đơn"].map(clean_text),
        "order_date": dates(raw["Ngày tháng"]),
        "status": raw["Trạng thái đơn"].map(map_status),
        "customer_name": raw["Đối tác"].map(strip_phone),
        "customer_key": raw["Đối tác"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "line_amount": money(raw["Giá trị hàng"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    df["line_kind"] = "product"
    df["is_return_line"] = False
    df["line_discount"] = 0

    brief = ["order list only: one line per order, revenue = the order's item total; no products"]
    return Converted(df, ["danh_sach_hoa_don.csv"], readme_data_end(shop_dir / "README.md"), notes, brief)
