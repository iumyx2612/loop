"""dev_007 (restaurant, CSV export): order and customer fields repeated on every line, codes on every line, daily
subtotal rows, "Phí phục vụ" service-fee lines, and no unit column (the unit follows from the converted quantity
and the README conversion list)."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_status, norm_text, parse_datetime, parse_qty, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, money, read_csv, readme_conversions,
    readme_data_end,
)

SERVICE_FEE = "phí phục vụ"


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_csv(shop_dir / "chi_tiet_hoa_don.csv")
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã bill", notes)

    catalog = read_csv(shop_dir / "danh_muc_hang.csv")
    products = pd.DataFrame({
        "product_code": catalog["Mã SKU"].map(clean_text),
        "product_name": catalog["Hàng bán"].map(clean_text),
        "base_unit": catalog["ĐVT"].map(norm_text),
    })

    df = pd.DataFrame({
        "order_id": raw["Mã bill"].map(clean_text),
        "line_no": pd.to_numeric(raw["No."]),
        "order_date": dates(raw["Ngày giao dịch"]),
        "order_time": raw["Giờ bán"].map(parse_datetime),
        "status": raw["Tiến độ"].map(map_status),
        "customer_key": raw["Mã khách mua"].map(clean_text),  # shop has customer codes (§3); blank = walk-in
        "customer_name": raw["Họ tên khách"].map(strip_phone),
        "customer_group": raw["Nhóm khách hàng"].map(norm_text),
        "channel": raw["Nguồn"].map(map_channel),
        "seller": raw["Nhân viên bán"].map(clean_text),
        "product_code": raw["Mã vạch"].map(clean_text),
        "product_group": raw["Phân loại"].map(norm_text),
        "qty": raw["Sluong"].map(lambda v: parse_qty(v)[0]),
        "qty_base": raw["Số lượng lẻ"].map(lambda v: parse_qty(v)[0]),
        "unit_price": money(raw["Đơn giá (đ)"]),
        "line_discount": money(raw["Tiền giảm"]),
        "line_amount": money(raw["Thành tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    if df["product_name"].isna().any():
        notes.append(f"{int(df['product_name'].isna().sum())} lines whose code is not in danh_muc_hang.csv")
        df["product_name"] = df["product_name"].fillna(raw["Mặt hàng"].map(clean_text))

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

    return Converted(df, ["chi_tiet_hoa_don.csv", "danh_muc_hang.csv", "README.md"], readme_data_end(shop_dir / "README.md"), notes)
