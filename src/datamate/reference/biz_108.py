"""biz_108 (grocery, POS export): line file + separate orders, customers and products files, joined by code."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_status, norm_text, parse_datetime, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_subtotals, money, parse_qty_and_unit,
    read_xlsx, readme_conversions, readme_data_end,
)


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / "DanhSachHoaDonChiTiet.xlsx")
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã hóa đơn", notes)

    orders = read_xlsx(shop_dir / "DanhSachHoaDon.xlsx")
    orders = pd.DataFrame({
        "order_id": orders["Mã hóa đơn"],
        "order_date": dates(orders["Thời gian"]),
        "order_time": orders["Thời gian tạo"].map(parse_datetime),
        "status": orders["Trạng thái"].map(map_status),
        "customer_code": orders["Mã KH"].map(clean_text),
        "customer_name": orders["Khách hàng"].map(strip_phone),
        "channel": orders["Kênh bán"].map(map_channel),
        "seller": orders["Người bán"].map(clean_text),  # staff code, e.g. NV003
    })
    customers = read_xlsx(shop_dir / "DanhSachKhachHang.xlsx")
    group_by_code = dict(zip(customers["Mã khách hàng"], customers["Nhóm khách hàng"].map(norm_text)))
    products = read_xlsx(shop_dir / "DanhSachSanPham.xlsx")
    products = pd.DataFrame({
        "product_code": products["Mã hàng"],
        "product_name": products["Tên hàng"].map(clean_text),
        "product_group": products["Nhóm hàng(3 Cấp)"].map(norm_text),
        "base_unit": products["ĐVT"].map(norm_text),
    })

    qty, unit = parse_qty_and_unit(raw["Số lượng"], raw["Đơn vị tính"])
    df = pd.DataFrame({
        "order_id": raw["Mã hóa đơn"],
        "line_no": pd.to_numeric(raw["STT"]),
        "product_code": raw["Mã hàng"],
        "line_name": raw["Tên hàng"],
        "unit": unit,
        "qty": qty,
        "qty_base": pd.to_numeric(raw["Số lượng quy đổi"]).astype(float),
        "unit_price": money(raw["Đơn giá"]),
        "line_discount": money(raw["Giảm giá"]),
        "line_amount": money(raw["Thành tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(orders, on="order_id", how="left", validate="many_to_one")
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    if df["status"].isna().any():
        notes.append(f"{int(df['status'].isna().sum())} lines whose order is not in DanhSachHoaDon.xlsx")
    if df["product_name"].isna().any():
        notes.append(f"{int(df['product_name'].isna().sum())} lines whose product code is not in DanhSachSanPham.xlsx")
        df["product_name"] = df["product_name"].fillna(df["line_name"])

    df["customer_key"] = df["customer_code"]  # shop has customer codes: one code = one customer (§3); blank = walk-in
    df["customer_group"] = df["customer_code"].map(group_by_code)
    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    # The file's converted quantity must agree with the README conversion list
    conv = conversion_lookup(readme_conversions(shop_dir / "README.md"))
    expected = [conv.get((norm_text(p), u), 1.0 if u == b else None) for p, u, b in zip(df["product_name"], df["unit"], df["base_unit"])]
    bad = (pd.Series(expected, index=df.index) != df["unit_factor"]).sum()
    if bad:
        notes.append(f"{int(bad)} lines where Số lượng quy đổi / Số lượng differs from the README conversion list")

    return Converted(df, ["DanhSachHoaDonChiTiet.xlsx", "DanhSachHoaDon.xlsx", "DanhSachKhachHang.xlsx",
                          "DanhSachSanPham.xlsx", "README.md"], readme_data_end(shop_dir / "README.md"), notes)
