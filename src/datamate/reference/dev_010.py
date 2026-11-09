"""dev_010 (mother & baby, CSV export): separate orders, lines, products and customers files joined by code; the
lines file repeats its header inside the data and the orders file has daily / monthly subtotal rows."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_payment, map_status, norm_text, parse_qty, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, money,
    parse_qty_and_unit, read_csv, readme_conversions, readme_data_end,
)


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_csv(shop_dir / "chi_tiet_don.csv")
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã bill", notes)

    orders = read_csv(shop_dir / "don_hang.csv")
    orders = drop_subtotals(orders, notes)
    orders = pd.DataFrame({
        "order_id": orders["Số CT"].map(clean_text),
        "order_date": dates(orders["Date"]),
        "status": orders["Xử lý"].map(map_status),
        "customer_code": orders["Mã thẻ"].map(clean_text),
        "channel": orders["Kênh đặt"].map(map_channel),
        "payment_method": orders["Phương thức TT"].map(map_payment),
    })
    if orders["order_id"].duplicated().any():
        notes.append(f"{int(orders['order_id'].duplicated().sum())} duplicate rows in don_hang.csv dropped")
        orders = orders.drop_duplicates()
    customers = read_csv(shop_dir / "khach_hang.csv")
    customers = pd.DataFrame({
        "customer_code": customers["Mã khách"].map(clean_text),
        "customer_name": customers["Khách lẻ/sỉ"].map(strip_phone),
        "customer_group": customers["Loại khách"].map(norm_text),
    })
    products = read_csv(shop_dir / "san_pham.csv")
    products = pd.DataFrame({
        "product_code": products["Mã vạch"].map(clean_text),
        "product_name": products["ten_hang"].map(clean_text),
        "product_group": products["Nhóm hàng"].map(norm_text),
        "base_unit": products["Đơn vị tính"].map(norm_text),
    })

    qty, unit = parse_qty_and_unit(raw["SoLuong"], raw["dvt"])
    df = pd.DataFrame({
        "order_id": raw["Mã bill"].map(clean_text),
        "line_no": pd.to_numeric(raw["No."]),
        "product_code": raw["Mã H.hóa"].map(clean_text),
        "unit": unit,
        "qty": qty,
        "qty_base": raw["SL quy đổi"].map(lambda v: parse_qty(v)[0]),
        "unit_price": money(raw["Đơn giá (đ)"]),
        "line_discount": money(raw["CK"]),
        "line_amount": money(raw["Cộng"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(orders, on="order_id", how="left", validate="many_to_one")
    df = df.merge(customers, on="customer_code", how="left", validate="many_to_one")
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    for col, src in (("status", "don_hang.csv"), ("product_name", "san_pham.csv")):
        if df[col].isna().any():
            notes.append(f"{int(df[col].isna().sum())} lines without a match in {src}")

    df["customer_key"] = df["customer_code"]  # shop has customer codes (§3); blank = walk-in
    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    conv = conversion_lookup(readme_conversions(shop_dir / "README.md"))
    expected = [conv.get((norm_text(p), u), 1.0 if u == b else None) for p, u, b in zip(df["product_name"], df["unit"], df["base_unit"])]
    bad = (pd.Series(expected, index=df.index) != df["unit_factor"]).sum()
    if bad:
        notes.append(f"{int(bad)} lines where SL quy đổi / SoLuong differs from the README conversion list")

    return Converted(df.drop(columns="customer_code"), ["chi_tiet_don.csv", "don_hang.csv", "khach_hang.csv", "san_pham.csv", "README.md"],
                     readme_data_end(shop_dir / "README.md"), notes)
