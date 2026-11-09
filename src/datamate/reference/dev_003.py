"""dev_003 (café, POS export + owner's notebooks): orders and lines share sheet `BanHang`, split by year into two
workbooks; products, customers and big units are sheets of the 2025 workbook, joined by code. Cost at sale is on
every line. Order ids sometimes carry a leading space."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_payment, map_status, norm_text, parse_datetime, parse_qty, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, money, parse_qty_and_unit,
    read_xlsx, readme_data_end,
)

BOOKS = ["BanHang_TongHop_2025.xlsx", "BanHang_TongHop_2026.xlsx"]


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = pd.concat([read_xlsx(shop_dir / b, sheets=["BanHang"]) for b in BOOKS], ignore_index=True)
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw["Đơn số"] = raw["Đơn số"].map(clean_text)
    raw = drop_duplicate_lines(raw, "Đơn số", notes)
    combined = raw["Hàng hóa"].astype("string").str.contains(" + ", regex=False).fillna(False)
    if combined.any():
        raise ValueError(f"{int(combined.sum())} lines with toppings in the drink's cell: not handled for this shop")

    first = shop_dir / BOOKS[0]
    products = read_xlsx(first, sheets=["BanHang_HangHoa"])
    products = pd.DataFrame({
        "product_code": products["Mã H.hóa"].map(clean_text),
        "product_name": products["Tên hàng hóa"].map(clean_text),
        "product_group": products["Nhóm hàng"].map(norm_text),
        "base_unit": products["DVT"].map(norm_text),
    })
    customers = read_xlsx(first, sheets=["BanHang_KhachHang"])
    group_by_code = dict(zip(customers["Mã KH mua"].map(clean_text), customers["Kiểu khách"].map(norm_text)))

    qty, unit = parse_qty_and_unit(raw["S.L"], raw["ĐV"])
    df = pd.DataFrame({
        "order_id": raw["Đơn số"],
        "line_no": pd.to_numeric(raw["Dòng"]),
        "order_date": dates(raw["Ngày HĐ"]),
        "order_time": raw["Giờ tạo đơn"].map(parse_datetime),
        "status": raw["Trạng thái giao"].map(map_status),
        "customer_code": raw["Mã khách"].map(clean_text),
        "customer_name": raw["Khách hàng (tên)"].map(strip_phone),
        "channel": raw["Nguồn"].map(map_channel),
        "payment_method": raw["Hình thức thanh toán"].map(map_payment),
        "seller": raw["Mã NV bán"].map(clean_text),
        "product_code": raw["Barcode"].map(clean_text),
        "line_name": raw["Hàng hóa"],
        "unit": unit,
        "qty": qty,
        "qty_base": raw["SL (ĐV cơ bản)"].map(lambda v: parse_qty(v)[0]),
        "unit_price": money(raw["Giá 1 đv"]),
        "line_discount": money(raw["Giảm (đ)"]),
        "line_amount": money(raw["Thành tiền (đ)"]),
        "cost_price": money(raw["Giá vốn lúc bán"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    if df["product_name"].isna().any():
        notes.append(f"{int(df['product_name'].isna().sum())} lines whose code is not in BanHang_HangHoa")
        df["product_name"] = df["product_name"].fillna(df["line_name"])

    df["customer_key"] = df["customer_code"]  # shop has customer codes (§3); blank = walk-in
    df["customer_group"] = df["customer_code"].map(group_by_code)
    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    return Converted(df.drop(columns=["customer_code", "line_name"]),
                     [f"{b} [BanHang]" for b in BOOKS] + [f"{BOOKS[0]} [BanHang_HangHoa, BanHang_KhachHang]"],
                     readme_data_end(shop_dir / "README.md"), notes)
