"""dev_005 (pharmacy): one file per month with a filter row and a title above the header; order fields only on an
order's first line (forward fill); product codes and groups from DanhSachSanPham.xlsx, joined by name.
DanhSachQuyDoi.xlsx gives each big unit in the unit just below it: a bare number is base units, "15 vỉ" is
15 of the product's vỉ. Returned quantities per lot are in DonHang_Lo.xlsx."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, customer_name_key, map_status, norm_text, strip_phone
from src.datamate.reference.common import (
    Converted, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals, forward_fill, implied_discount,
    match_names, money, parse_qty_and_unit, read_xlsx, readme_data_end,
)

ORDER_COLS = ["Mã đơn hàng", "Ngày đặt hàng", "Trạng thái đơn hàng", "Tên khách hàng"]


def nested_factors(qd: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(product, big unit) -> base units, resolving "15 vỉ" through the product's vỉ factor."""
    raw = {(norm_text(p), norm_text(u)): f for p, u, f in zip(qd["Tên sản phẩm"], qd["Đơn vị quy đổi"], qd["Tỉ lệ quy đổi"])}

    def resolve(key, depth=0):
        f = raw[key]
        if isinstance(f, (int, float)):
            return float(f)
        m = re.fullmatch(r"\s*([\d.,]+)\s+(.+?)\s*", str(f))
        if not m or depth > 3:
            raise ValueError(f"unrecognised conversion {f!r} for {key}")
        return float(m[1].replace(",", ".")) * resolve((key[0], norm_text(m[2])), depth + 1)

    return {k: resolve(k) for k in raw}


def convert(shop_dir: Path) -> Converted:
    notes = []
    files = sorted(shop_dir.glob("BanHang_Thang*_*.xlsx"))
    # Rows 1-3: "Trạng thái đơn hàng | Đặt hàng (Đã duyệt)" filter row, period, blank row
    raw = pd.concat([read_xlsx(f, header_row=4) for f in files], ignore_index=True)
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = forward_fill(raw, ORDER_COLS, notes)
    raw = drop_duplicate_lines(raw, "Mã đơn hàng", notes)

    products = read_xlsx(shop_dir / "DanhSachSanPham.xlsx")
    products = pd.DataFrame({
        "product_name": products["Tên sản phẩm"].map(clean_text),
        "product_code": products["Mã SKU"].map(clean_text),
        "product_group": products["Loại sản phẩm"].map(norm_text),
        "base_unit": products["Đơn vị"].map(norm_text),
    })
    conv = nested_factors(read_xlsx(shop_dir / "DanhSachQuyDoi.xlsx"))

    qty, unit = parse_qty_and_unit(raw["Số lượng sản phẩm"], raw["Đơn vị tính"])
    df = pd.DataFrame({
        "order_id": raw["Mã đơn hàng"].map(clean_text),
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
                     f"{sorted(raw.loc[missing.values, 'Tên sản phẩm'].astype(str).unique())[:5]}")

    # Cross-check returns: an order with returned quantity in the lot file must carry a return status
    lots = read_xlsx(shop_dir / "DonHang_Lo.xlsx")
    lot_returned = set(lots.loc[pd.to_numeric(lots["Số lượng trả"]) > 0, "Mã đơn hàng"].map(clean_text))
    has_return = df["order_id"].isin(lot_returned)
    disagree = has_return & (df["status"] != "returned")
    if disagree.any():
        notes.append(f"{df.loc[disagree, 'order_id'].nunique()} orders with returns in DonHang_Lo.xlsx but a non-return status: set to returned")
        df.loc[has_return, "status"] = "returned"
    other = df["order_id"][(df["status"] == "returned") & ~has_return].nunique()
    if other:
        notes.append(f"{other} orders with a return status but no returned quantity in DonHang_Lo.xlsx")

    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    name_key = df["product_name"].map(norm_text)
    df["unit_factor"] = [1.0 if u == b else conv.get((k, u)) for k, u, b in zip(name_key, df["unit"], df["base_unit"])]
    df["qty_base"] = df["qty"] * df["unit_factor"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    return Converted(df, [f.name for f in files] + ["DanhSachSanPham.xlsx", "DanhSachQuyDoi.xlsx", "DonHang_Lo.xlsx"],
                     readme_data_end(shop_dir / "README.md"), notes)
