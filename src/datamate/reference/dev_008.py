"""dev_008 (restaurant, xlsx export split in two files): order and customer fields repeated on every line, codes on
every line, daily subtotal rows and "Phí phục vụ" service-fee lines; product list joined by code."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_payment, map_status, norm_text, parse_datetime, parse_qty, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, money, parse_qty_and_unit, read_xlsx,
    readme_conversions, conversion_lookup, readme_data_end,
)

LINE_FILES = ["ChiTietHoaDon_1.xlsx", "ChiTietHoaDon_2.xlsx"]
SERVICE_FEE = "phí phục vụ"


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = pd.concat([read_xlsx(shop_dir / f) for f in LINE_FILES], ignore_index=True)
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Bill", notes)

    catalog = read_xlsx(shop_dir / "DanhMucHang.xlsx")
    products = pd.DataFrame({
        "product_code": catalog["Mã sản phẩm bán"].map(clean_text),
        "product_name": catalog["Hàng"].map(clean_text),
        "base_unit": catalog["DVT"].map(norm_text),
    })

    qty, unit = parse_qty_and_unit(raw["SLg"], raw["Đơn vị (tính)"])
    df = pd.DataFrame({
        "order_id": raw["Bill"].map(clean_text),
        "line_no": pd.to_numeric(raw["Số dòng"]),
        "order_date": dates(raw["Ngày xuất"]),
        "order_time": raw["Giờ bán"].map(parse_datetime),
        "status": raw["TT đơn"].map(map_status),
        "customer_key": raw["Mã người mua"].map(clean_text),  # shop has customer codes (§3); blank = walk-in
        "customer_name": raw["Khách"].map(strip_phone),
        "customer_group": raw["Nhóm khách hàng"].map(norm_text),
        "channel": raw["Kênh bán"].map(map_channel),
        "payment_method": raw["Thanh toán bằng"].map(map_payment),
        "seller": raw["Nhân viên bán"].map(clean_text),
        "product_code": raw["Ma hang"].map(clean_text),
        "product_group": raw["Nhóm hàng"].map(norm_text),
        "unit": unit,
        "qty": qty,
        "qty_base": raw["SL lẻ"].map(lambda v: parse_qty(v)[0]),
        "unit_price": money(raw["Đơn giá (đ)"]),
        "line_discount": money(raw["Giảm (đ)"]),
        "line_amount": money(raw["Thành tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    if df["product_name"].isna().any():
        notes.append(f"{int(df['product_name'].isna().sum())} lines whose code is not in DanhMucHang.xlsx")
        df["product_name"] = df["product_name"].fillna(raw["Tên hàng"].map(clean_text))

    df["product_key"] = df["product_code"]
    df["line_kind"] = df["product_name"].map(lambda v: "service_fee" if norm_text(v) == SERVICE_FEE else "product")
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    conv = conversion_lookup(readme_conversions(shop_dir / "README.md"))
    expected = [conv.get((norm_text(p), u), 1.0 if u == b else None) for p, u, b in zip(df["product_name"], df["unit"], df["base_unit"])]
    bad = (pd.Series(expected, index=df.index) != df["unit_factor"]).sum()
    if bad:
        notes.append(f"{int(bad)} lines where SL lẻ / SLg differs from the README conversion list")

    return Converted(df, LINE_FILES + ["DanhMucHang.xlsx", "README.md"], readme_data_end(shop_dir / "README.md"), notes)
