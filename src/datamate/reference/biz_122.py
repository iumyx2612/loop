"""biz_122 (pharmacy): order lines split over two files with a sheet per month and a filter row above the
header; order fields only on an order's first line (forward fill); conversions in DanhSachQuyDoi.xlsx;
product codes and groups from DanhSachSanPham.xlsx, joined by name."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, customer_name_key, map_status, norm_text, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_subtotals, forward_fill, implied_discount,
    match_names, money, parse_qty_and_unit, read_xlsx, readme_data_end,
)

LINE_FILES = ["DonHang_ChiTiet_1.xlsx", "DonHang_ChiTiet_2026.xlsx"]
ORDER_COLS = ["Mã đơn hàng", "Ngày đặt hàng", "Trạng thái đơn hàng", "Tên khách hàng"]


def convert(shop_dir: Path) -> Converted:
    notes = []
    # Rows 1-2: "Trạng thái đơn hàng | Đặt hàng (Đã duyệt)" filter row and a blank row
    raw = pd.concat([read_xlsx(shop_dir / f, header_row=3) for f in LINE_FILES], ignore_index=True)
    raw = drop_subtotals(raw, notes)
    raw = forward_fill(raw, ORDER_COLS, notes)
    raw = drop_duplicate_lines(raw, "Mã đơn hàng", notes)

    products = read_xlsx(shop_dir / "DanhSachSanPham.xlsx")
    products = pd.DataFrame({
        "product_name": products["Tên sản phẩm"].map(clean_text),
        "product_code": products["Mã SKU"],
        "product_group": products["Loại sản phẩm"].map(norm_text),
        "base_unit": products["Đơn vị"].map(norm_text),
    })
    qd = read_xlsx(shop_dir / "DanhSachQuyDoi.xlsx")
    conv = conversion_lookup(pd.DataFrame({"product": qd["Tên sản phẩm"], "unit": qd["Đơn vị quy đổi"], "factor": qd["Tỉ lệ quy đổi"]}))

    qty, unit = parse_qty_and_unit(raw["Số lượng sản phẩm"], raw["Đơn vị tính"])
    df = pd.DataFrame({
        "order_id": raw["Mã đơn hàng"],
        "order_date": dates(raw["Ngày đặt hàng"]),
        "status": raw["Trạng thái đơn hàng"].map(map_status),
        "customer_name": raw["Tên khách hàng"].map(strip_phone),
        "customer_key": raw["Tên khách hàng"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "product_name": match_names(raw["Tên sản phẩm"], products["product_name"]),
        "unit": unit,
        "qty": qty,
        "unit_price": money(raw["Giá sản phẩm"]),
        "line_amount": money(raw["Tổng tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_name", how="left", validate="many_to_one")
    assert len(df) == n
    missing = df["product_code"].isna()
    if missing.any():
        notes.append(f"{int(missing.sum())} lines whose product is not in DanhSachSanPham.xlsx: "
                     f"{sorted(raw.loc[missing.values, 'Tên sản phẩm'].unique())[:5]}")

    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0  # returns are a status here (lot file agrees: checked), not negative lines
    name_key = df["product_name"].map(norm_text)
    df["unit_factor"] = [1.0 if u == b else conv.get((k, u)) for k, u, b in zip(name_key, df["unit"], df["base_unit"])]
    df["qty_base"] = df["qty"] * df["unit_factor"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    return Converted(df, LINE_FILES + ["DanhSachSanPham.xlsx", "DanhSachQuyDoi.xlsx"], readme_data_end(shop_dir / "README.md"), notes)
