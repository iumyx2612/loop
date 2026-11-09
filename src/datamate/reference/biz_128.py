"""biz_128 (restaurant, POS export): like biz_108 (orders, customers, products joined by code), plus
"Phí phục vụ" service-fee lines, orders split one sheet per month, and no unit column on the lines."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_status, norm_text, parse_datetime, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_subtotals, money, read_xlsx, readme_conversions, readme_data_end,
)

SERVICE_FEE = "phí phục vụ"


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / "DanhSachHoaDonChiTiet.xlsx")
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã hóa đơn", notes)

    orders = read_xlsx(shop_dir / "DanhSachHoaDon.xlsx")  # one sheet per month
    orders = pd.DataFrame({
        "order_id": orders["Mã hóa đơn"],
        "order_date": dates(orders["Thời gian"]),
        "order_time": orders["Thời gian tạo"].map(parse_datetime),
        "status": orders["Trạng thái"].map(map_status),
        "customer_code": orders["Mã KH"].map(clean_text),
        "customer_name": orders["Khách hàng"].map(strip_phone),
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

    df = pd.DataFrame({
        "order_id": raw["Mã hóa đơn"],
        "line_no": pd.to_numeric(raw["STT"]),
        "product_code": raw["Mã hàng"],
        "qty": pd.to_numeric(raw["Số lượng"]).astype(float),
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
    for col, src in (("status", "DanhSachHoaDon.xlsx"), ("product_name", "DanhSachSanPham.xlsx")):
        if df[col].isna().any():
            notes.append(f"{int(df[col].isna().sum())} lines without a match in {src}")

    df["customer_key"] = df["customer_code"]  # shop has customer codes (§3); blank = walk-in
    df["customer_group"] = df["customer_code"].map(group_by_code)
    df["product_key"] = df["product_code"]
    df["line_kind"] = df["product_name"].map(lambda v: "service_fee" if norm_text(v) == SERVICE_FEE else "product")
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    # No unit column: factor 1 = the base unit; otherwise the README's big unit with that factor for the product
    conv = readme_conversions(shop_dir / "README.md")
    big_unit = {(norm_text(r.product), r.factor): norm_text(r.unit) for r in conv.itertuples()}
    df["unit"] = [b if f == 1 else big_unit.get((norm_text(p), f))
                  for p, f, b in zip(df["product_name"], df["unit_factor"], df["base_unit"])]
    if (df["unit_factor"] != 1).any():
        notes.append(f"{int((df['unit_factor'] != 1).sum())} lines in a big unit: unit taken from the README list by factor")

    return Converted(df, ["DanhSachHoaDonChiTiet.xlsx", "DanhSachHoaDon.xlsx", "DanhSachKhachHang.xlsx",
                          "DanhSachSanPham.xlsx", "README.md"], readme_data_end(shop_dir / "README.md"), notes)
