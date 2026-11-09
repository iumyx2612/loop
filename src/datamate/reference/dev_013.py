"""dev_013 (mother & baby, owner's books): sales books per year with one sheet per month and a group row above the
header; dates with and without a year; footer rows. After an order's product lines there can be rows that are not
products ("Voucher", "Dùng <n> điểm", "Phí ship"): order discounts and shipping, not revenue (rulebook §1), so they
are dropped here. No status column: the order's Ghi chú holds it (§8: no mention = completed). Products are matched
by name to QuanLy.xlsx [DanhMuc], which gives the group, base unit and big units ("1 thùng = 4 bịch; ...")."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import STATUS_COMPLETED, customer_name_key, is_blank, map_channel, map_status, norm_text, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_subtotals, implied_discount, match_names, money, parse_qty_and_unit,
    read_xlsx, readme_data_end,
)

BOOKS = ["BanHang_2025.xlsx", "BanHang_2026.xlsx"]
NOT_PRODUCT = re.compile(r"^\s*(voucher|phí ship|dùng\s+\d+\s+điểm)\s*$", re.IGNORECASE)
BIG_UNIT = re.compile(r"\s*1\s+(.+?)\s*=\s*([\d.,]+)\s+(.+?)\s*$")


def conversions(catalog: pd.DataFrame) -> dict[tuple[str, str], float]:
    """'1 thùng = 4 bịch; 1 lốc = 4 gói' -> {(product, 'thùng'): 4.0, ...}; every factor is in the base unit."""
    out = {}
    for name, base, text in zip(catalog["Tên hàng"], catalog["ĐVT"], catalog["Quy đổi"]):
        if is_blank(text):
            continue
        for part in str(text).split(";"):
            m = BIG_UNIT.fullmatch(part)
            if not m or norm_text(m[3]) != norm_text(base):
                raise ValueError(f"unrecognised conversion {part!r} for {name!r} (base unit {base!r})")
            out[(norm_text(name), norm_text(m[1]))] = float(m[2].replace(",", "."))
    return out


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = pd.concat([read_xlsx(shop_dir / b, header_row=2) for b in BOOKS], ignore_index=True)  # row 1: Tiền · Hàng hoá · Hoá đơn
    raw = drop_subtotals(raw, notes)
    not_product = raw["Tên hàng"].map(lambda v: isinstance(v, str) and bool(NOT_PRODUCT.match(v)))
    notes.append(f"dropped {int(not_product.sum())} Voucher / Dùng điểm / Phí ship rows (not products, not revenue)")
    note_by_order = raw["Ghi chú"].where(~raw["Ghi chú"].map(is_blank)).groupby(raw["HĐ"]).first()
    raw = raw[~not_product]
    raw = drop_duplicate_lines(raw, "HĐ", notes)

    catalog = read_xlsx(shop_dir / "QuanLy.xlsx", sheets=["DanhMuc"])
    conv = conversions(catalog)
    products = pd.DataFrame({
        "product_name": catalog["Tên hàng"].map(lambda v: str(v).strip()),
        "product_group": catalog["Nhóm"].map(norm_text),
        "base_unit": catalog["ĐVT"].map(norm_text),
    })

    # Rulebook §8: a date without a year takes the year of its sheet ("T05-2025")
    sheet_year = raw["source_sheet"].map(lambda s: int(re.search(r"(\d{4})", s)[1]))
    qty, unit = parse_qty_and_unit(raw["SL"], raw["ĐVT"])
    df = pd.DataFrame({
        "order_id": raw["HĐ"],
        "order_date": dates(raw["Ngày"], sheet_year),
        "status": raw["HĐ"].map(note_by_order).map(lambda v: STATUS_COMPLETED if is_blank(v) else map_status(v)),
        "customer_name": raw["Khách"].map(strip_phone),
        "customer_key": raw["Khách"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "channel": raw["Kênh"].map(map_channel),
        "product_name": match_names(raw["Tên hàng"], products["product_name"]),
        "unit": unit,
        "qty": qty,
        "unit_price": money(raw["ĐG"]),
        "line_amount": money(raw["TT"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(products, on="product_name", how="left", validate="many_to_one")
    assert len(df) == n
    if df["product_group"].isna().any():
        notes.append(f"{int(df['product_group'].isna().sum())} lines whose product is not in QuanLy.xlsx [DanhMuc]")

    df["product_key"] = df["product_name"].map(norm_text)
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = [1.0 if u == b else conv.get((k, u)) for k, u, b in zip(df["product_key"], df["unit"], df["base_unit"])]
    df["qty_base"] = df["qty"] * df["unit_factor"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    brief = ["vouchers, points used and shipping fees are not sales lines (not revenue); questions about them, about "
             "brands, milk numbers / diaper sizes or members are OUT OF SCOPE"]
    return Converted(df, BOOKS + ["QuanLy.xlsx [DanhMuc]"], readme_data_end(shop_dir / "README.md"), notes, brief)
