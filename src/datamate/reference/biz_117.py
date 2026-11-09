"""biz_117 (building materials, hand-written book): one sheet per month with a group row above the header,
footer rows, dates without a year, money like 1tr440, returns as negative-quantity lines of the same order.
No product codes: products are identified by name (the stock list gives the canonical spelling)."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import customer_name_key, map_status, norm_text, parse_datetime, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_subtotals, implied_discount, match_names, money,
    parse_qty_and_unit, read_xlsx, readme_data_end,
)


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / "SoBanHang.xlsx", header_row=2)  # row 1 is a group row: Đơn · Tiền · Hàng
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã phiếu", notes)

    catalog = read_xlsx(shop_dir / "TonKho.xlsx")  # every product with its base unit
    danhmuc = read_xlsx(shop_dir / "DanhMuc.xlsx")  # products that have a big unit
    conv = conversion_lookup(pd.DataFrame({"product": danhmuc["Hàng"], "unit": danhmuc["ĐV lớn"], "factor": danhmuc["Hệ số QĐ"]}))
    base_by_name = dict(zip(catalog["Product"], catalog["Đơn vị"].map(norm_text)))

    # Rulebook §8: a date without a year takes the year of its sheet ("Tháng 01-2025")
    sheet_year = raw["source_sheet"].map(lambda s: int(re.search(r"(\d{4})", s)[1]))
    qty, unit = parse_qty_and_unit(raw["Số lượng hàng"], raw["DonVi"])
    df = pd.DataFrame({
        "order_id": raw["Mã phiếu"],
        "line_date": dates(raw["Ngày lập"], sheet_year),
        "line_time": raw["Giờ"].map(parse_datetime),
        "status": raw["Ghi chú TT"].map(map_status),
        "customer_name": raw["TenKH"].map(strip_phone),
        "customer_key": raw["TenKH"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "product_name": match_names(raw["Tên SP"], catalog["Product"]),
        "unit": unit,
        "qty": qty,
        "unit_price": money(raw["Giá"]),
        "line_amount": money(raw["Tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    missing = df["product_name"].isna()
    if missing.any():
        notes.append(f"{int(missing.sum())} lines whose product is not in TonKho.xlsx: {sorted(raw.loc[missing, 'Tên SP'].unique())[:5]}")
        df["product_name"] = df["product_name"].fillna(raw["Tên SP"])

    df["is_return_line"] = df["qty"] < 0
    # Return lines carry the date and time of the return; the order's date and time come from its sale lines
    sale = df[~df["is_return_line"]]
    df["order_date"] = df["order_id"].map(sale.groupby("order_id")["line_date"].min())
    df["order_time"] = df["order_id"].map(sale.groupby("order_id")["line_time"].min())
    df["return_date"] = df["line_date"].where(df["is_return_line"])
    if df["order_date"].isna().any():
        notes.append(f"{int(df['order_date'].isna().sum())} lines of orders that have only return lines")

    # The README says an order with a negative-quantity line is a returned order (rulebook §1)
    has_return = df.groupby("order_id")["is_return_line"].transform("any")
    disagree = has_return & (df["status"] != "returned")
    if disagree.any():
        notes.append(f"{int(disagree.sum())} lines of orders with return lines but a non-return status: set to returned")
        df.loc[has_return, "status"] = "returned"

    df["product_key"] = df["product_name"].map(norm_text)
    df["base_unit"] = df["product_name"].map(base_by_name)
    df["line_kind"] = "product"
    name_key = df["product_name"].map(norm_text)
    df["unit_factor"] = [1.0 if u == b else conv.get((k, u)) for k, u, b in zip(name_key, df["unit"], df["base_unit"])]
    df["qty_base"] = df["qty"] * df["unit_factor"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    return Converted(df, ["SoBanHang.xlsx", "TonKho.xlsx", "DanhMuc.xlsx"], readme_data_end(shop_dir / "README.md"), notes)
