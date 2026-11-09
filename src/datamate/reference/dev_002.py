"""dev_002 (café): one file per month with a title block above the header, the header repeated inside the data,
daily subtotal rows, no product list (names only, many spellings), conversions in DanhSachQuyDoi.xlsx.
Toppings can share the drink's cell ("Latte (M) + 2 Thạch cà phê (14.000đ)"): rulebook §6 Cà phê makes them
separate products, the same as the "Topping <name>" lines, so each one becomes its own line here."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import customer_name_key, map_status, norm_text, parse_money, strip_phone
from src.datamate.reference.common import (
    Converted, canonical_names, conversion_lookup, dates, drop_duplicate_lines, drop_repeated_headers, drop_subtotals,
    implied_discount, money, parse_qty_and_unit, read_xlsx, readme_data_end,
)

TOPPING = re.compile(r"^(?:(\d+)\s+)?(.+?)\s*\(([^()]*)\)$")  # "[n ]<topping> (<money>)"
TOPPING_UNIT = "phần"  # unit of the "Topping <name>" lines


def split_toppings(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """One combined line -> the drink line (amount minus toppings) + one line per topping."""
    out = []
    n_combined = 0
    for rec in df.to_dict("records"):
        parts = [p.strip() for p in str(rec["raw_name"]).split(" + ")]
        if len(parts) == 1:
            out.append(rec)
            continue
        n_combined += 1
        toppings = []
        for p in parts[1:]:
            m = TOPPING.match(p)
            if not m:
                raise ValueError(f"unrecognised topping {p!r} in {rec['raw_name']!r}")
            n, amount = int(m[1] or 1), parse_money(m[3])
            toppings.append({**rec, "raw_name": f"Topping {m[2]}", "unit": TOPPING_UNIT, "qty": float(n),
                             "unit_price": amount / n, "line_amount": amount})
        out.append({**rec, "raw_name": parts[0], "line_amount": rec["line_amount"] - sum(t["line_amount"] for t in toppings)})
        out.extend(toppings)
    if n_combined:
        notes.append(f"split toppings out of {n_combined} combined drink lines")
    return pd.DataFrame(out)


def convert(shop_dir: Path) -> Converted:
    notes = []
    files = sorted(shop_dir.glob("BanHang_T*_*.xlsx"))
    raw = pd.concat([read_xlsx(f, header_row=4) for f in files], ignore_index=True)  # rows 1-3: title, period, blank
    raw = drop_repeated_headers(raw, notes)
    raw = drop_subtotals(raw, notes)
    raw = drop_duplicate_lines(raw, "Mã đơn hàng", notes)

    qd = read_xlsx(shop_dir / "DanhSachQuyDoi.xlsx")
    conv = conversion_lookup(pd.DataFrame({"product": qd["Tên sản phẩm"], "unit": qd["Đơn vị quy đổi"], "factor": qd["Tỉ lệ quy đổi"]}))
    base_by_name = dict(zip(qd["Tên sản phẩm"].map(norm_text), qd["Đơn vị cơ bản"].map(norm_text)))

    qty, unit = parse_qty_and_unit(raw["Số lượng sản phẩm"], raw["Đơn vị tính"])
    df = pd.DataFrame({
        "order_id": raw["Mã đơn hàng"],
        "order_date": dates(raw["Ngày đặt hàng"]),
        "status": raw["Trạng thái đơn hàng"].map(map_status),
        "customer_name": raw["Tên khách hàng"].map(strip_phone),
        "customer_key": raw["Tên khách hàng"].map(customer_name_key),  # no customer codes: identify by name (§3)
        "raw_name": raw["Tên sản phẩm"],
        "unit": unit,
        "qty": qty,
        "unit_price": money(raw["Giá sản phẩm"]),
        "line_amount": money(raw["Tổng tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    df = split_toppings(df, notes)

    # No product list: every spelling of a name maps to its most frequent spelling (§8)
    df["product_name"] = canonical_names(df["raw_name"])
    df["product_key"] = df["product_name"].map(norm_text)
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    # Only products in the conversion file have a big unit; for the others the unit written is the base unit
    df["base_unit"] = [base_by_name.get(k, u) for k, u in zip(df["product_key"], df["unit"])]
    df["unit_factor"] = [1.0 if u == b else conv.get((k, u)) for k, u, b in zip(df["product_key"], df["unit"], df["base_unit"])]
    df["qty_base"] = df["qty"] * df["unit_factor"]
    df["line_discount"] = implied_discount(df["qty"], df["unit_price"], df["line_amount"])

    return Converted(df.drop(columns="raw_name"), [f.name for f in files] + ["DanhSachQuyDoi.xlsx"],
                     readme_data_end(shop_dir / "README.md"), notes)
