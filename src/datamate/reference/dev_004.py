"""dev_004 (building materials, hand-written book): one sheet per month with a group row above the header; order
fields only on an order's first line (forward fill); dates with and without a year; footer rows; returns as
negative-quantity lines of the same order, dated on the day of the return. No product codes: products are
identified by name (the stock list gives the canonical spelling and the base unit)."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import customer_name_key, map_status, norm_text, parse_datetime, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_subtotals, forward_fill, implied_discount,
    match_names, money, parse_qty_and_unit, read_xlsx, readme_data_end,
)

ORDER_COLS = ["Ma don", "Khách hàng (tên)", "Thời gian", "Trạng thái xử lý"]
# README "Hàng theo bó": these pipes are sold by the bundle, 1 bó = 10 cây
BUNDLE = {"Ống PVC Bình Minh D32 cây 4m": 10.0, "Ống PVC Bình Minh D50 cây 4m": 10.0, "Ống PVC Bình Minh D90 cây 4m": 10.0}


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / "SoBanHang.xlsx", header_row=2)  # row 1 is a group row: Đơn · Hàng · Tiền
    raw = drop_subtotals(raw, notes)
    raw = forward_fill(raw, ORDER_COLS, notes)
    raw = drop_duplicate_lines(raw, "Ma don", notes)

    catalog = read_xlsx(shop_dir / "TonKho.xlsx")  # every product with its base unit
    danhmuc = read_xlsx(shop_dir / "DanhMuc.xlsx")  # products that have a big unit
    conv = conversion_lookup(pd.concat([
        pd.DataFrame({"product": danhmuc["Hàng"], "unit": danhmuc["Loại ĐV"], "factor": danhmuc["Hệ số"]}),
        pd.DataFrame({"product": list(BUNDLE), "unit": "bó", "factor": list(BUNDLE.values())}),
    ]))
    base_by_name = dict(zip(catalog["Tên sản phẩm"], catalog["ĐV gốc"].map(norm_text)))

    # Rulebook §8: a date without a year takes the year of its sheet ("T01-2025")
    sheet_year = raw["source_sheet"].map(lambda s: int(re.search(r"(\d{4})", s)[1]))
    qty, unit = parse_qty_and_unit(raw["SL bán"], raw["dvt"])
    df = pd.DataFrame({
        "order_id": raw["Ma don"],
        "line_date": dates(raw["Thời gian"], sheet_year),
        "line_time": raw["Giờ"].map(parse_datetime),
        "status": raw["Trạng thái xử lý"].map(map_status),
        "customer_name": raw["Khách hàng (tên)"].map(strip_phone),
        "customer_key": raw["Khách hàng (tên)"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "product_name": match_names(raw["Hàng bán"], catalog["Tên sản phẩm"]),
        "unit": unit,
        "qty": qty,
        "unit_price": money(raw["Giá 1 đv"]),
        "line_amount": money(raw["Amount"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    missing = df["product_name"].isna()
    if missing.any():
        notes.append(f"{int(missing.sum())} lines whose product is not in TonKho.xlsx: {sorted(raw.loc[missing, 'Hàng bán'].unique())[:5]}")
        df["product_name"] = df["product_name"].fillna(raw["Hàng bán"])

    df["is_return_line"] = df["qty"] < 0
    # Return lines carry the date and time of the return; the order's date and time come from its sale lines
    sale = df[~df["is_return_line"]]
    df["order_date"] = df["order_id"].map(sale.groupby("order_id")["line_date"].min())
    df["order_time"] = df["order_id"].map(sale.groupby("order_id")["line_time"].min())
    df["return_date"] = df["line_date"].where(df["is_return_line"])
    if df["order_date"].isna().any():
        notes.append(f"{int(df['order_date'].isna().sum())} lines of orders that have only return lines")

    # README: an order with a negative-quantity line is an order with a return (rulebook §1: returned as a whole)
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

    return Converted(df.drop(columns=["line_date", "line_time"]), ["SoBanHang.xlsx", "TonKho.xlsx", "DanhMuc.xlsx", "README.md"],
                     readme_data_end(shop_dir / "README.md"), notes)
