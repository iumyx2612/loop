"""dev_001 (grocery, POS export): sales book with a title block above the header, order fields on every
line, product names only (codes and groups from the product list, joined by name); conversions in the README."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, customer_name_key, map_status, norm_text, parse_qty, strip_phone
from src.datamate.reference.common import (
    Converted, conversion_lookup, dates, drop_duplicate_lines, drop_subtotals, implied_discount, match_names, money,
    parse_qty_and_unit, read_xlsx, readme_conversions, readme_data_end,
)

LINES = "So_chi_tiet_ban_hang.xlsx"
PRODUCTS = "Danh sách hàng hóa.xlsx"


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / LINES, header_row=4)  # rows 1-3: title, "Kỳ báo cáo", blank
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã đơn hàng", notes)

    products = read_xlsx(shop_dir / PRODUCTS)
    products = pd.DataFrame({
        "product_code": products["Mã hàng hóa"],
        "product_name": products["Tên hàng hóa"].map(clean_text),
        "product_group": products["Nhóm hàng hóa"].map(norm_text),
        "base_unit": products["Đơn vị tính"].map(norm_text),
    })

    qty, unit = parse_qty_and_unit(raw["Số lượng"], raw["Đơn vị tính"])
    df = pd.DataFrame({
        "order_id": raw["Mã đơn hàng"].map(clean_text),
        "order_date": dates(raw["Ngày"]),
        "status": raw["Trạng thái"].map(map_status),
        "customer_name": raw["Khách hàng"].map(strip_phone),
        "customer_key": raw["Khách hàng"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "product_name": match_names(raw["Tên hàng hóa"], products["product_name"]),
        "unit": unit,
        "qty": qty,
        "qty_base": raw["Số lượng quy đổi"].map(lambda v: parse_qty(v)[0]),
        "unit_price": money(raw["Đơn giá"]),
        "line_amount": money(raw["Thành tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_name", how="left", validate="many_to_one")
    assert len(df) == n
    missing = df["product_code"].isna()
    if missing.any():
        notes.append(f"{int(missing.sum())} lines whose product is not in {PRODUCTS}: "
                     f"{sorted(raw.loc[missing.values, 'Tên hàng hóa'].astype(str).unique())[:5]}")

    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    conv = conversion_lookup(readme_conversions(shop_dir / "README.md"))
    expected = [conv.get((norm_text(p), u), 1.0 if u == b else None) for p, u, b in zip(df["product_name"], df["unit"], df["base_unit"])]
    bad = (pd.Series(expected, index=df.index) != df["unit_factor"]).sum()
    if bad:
        notes.append(f"{int(bad)} lines where Số lượng quy đổi / Số lượng differs from the README conversion list")

    return Converted(df, [LINES, PRODUCTS, "README.md"], readme_data_end(shop_dir / "README.md"), notes)
