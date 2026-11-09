"""Shared by dev_011 and dev_014 (phone shops, owner's sales book): sheet `Ghi tắt` maps each way of writing a product
to its full name and kind (máy mới / máy cũ / phụ kiện, used as the product group); the lines of one order share
an STT. No status column: the order's Ghi chú holds it (rulebook §8: no mention = completed). No unit column:
each line counts pieces (phones, accessories), so the quantity is already in the base unit."""
import pandas as pd

from src.datamate.parse import STATUS_COMPLETED, customer_name_key, fold_accents, is_blank, map_status, norm_text, strip_phone
from src.datamate.reference.common import dates, money


BRIEF = ["phone shop: product groups are the kind of item: máy mới (new phone), máy cũ (used phone), phụ kiện "
         "(accessory); phones count in chiếc. Trade-ins, amounts paid, debt, instalments and warranty are not in the "
         "sales lines: OUT OF SCOPE"]


def product_lookup(abbrev: pd.DataFrame) -> tuple[dict, dict]:
    """fold_accents(abbreviation or full name) -> full name, and full name -> kind. Some lines write the
    abbreviation or the name without accents ("Op silicon IP"), so matching ignores accents."""
    name_of, kind_of = {}, {}
    for short, full, kind in zip(abbrev["Ghi tắt"], abbrev["Tên hàng"], abbrev["Loại"]):
        full = str(full).strip()
        for k in (fold_accents(short), fold_accents(full)):
            if name_of.get(k, full) != full:
                raise ValueError(f"{k!r} means both {name_of[k]!r} and {full!r}")
            name_of[k] = full
        kind_of[full] = norm_text(kind)
    return name_of, kind_of


def order_status(note) -> str:
    return STATUS_COMPLETED if is_blank(note) else map_status(note)


def convert_book(raw: pd.DataFrame, abbrev: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """raw: book rows with an `order_no` column (the STT, filled on every line of the order)."""
    name_of, kind_of = product_lookup(abbrev)
    product_name = raw["Hàng"].map(lambda v: name_of.get(fold_accents(v)))
    if product_name.isna().any():
        raise ValueError(f"products not in Ghi tắt: {sorted(raw.loc[product_name.isna(), 'Hàng'].astype(str).unique())[:10]}")

    # Order-level cells may be written on the order's first line only: take the first non-blank one of the order
    def per_order(col):
        first = raw[col].where(~raw[col].map(is_blank)).groupby(raw["order_no"]).first()
        return raw["order_no"].map(first)

    customer = per_order("Khách")
    qty = pd.to_numeric(raw["SL"]).astype(float)
    df = pd.DataFrame({
        "order_id": raw["order_no"].map(lambda n: f"STT{int(n)}"),
        "order_date": dates(per_order("Ngày")),
        "status": per_order("Ghi chú").map(order_status),
        "customer_name": customer.map(strip_phone),
        "customer_key": customer.map(customer_name_key),  # no customer codes: identify by name (§3)
        "product_name": product_name,
        "product_group": product_name.map(kind_of),
        "qty": qty,
        "unit_factor": 1.0,
        "qty_base": qty,
        "unit_price": money(raw["Giá"]),
        "line_amount": money(raw["Thành tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    df["product_key"] = df["product_name"].map(norm_text)
    # Assumption (the book has no unit): phones count in chiếc, accessories in cái, one per SL
    df["unit"] = df["base_unit"] = df["product_group"].map(lambda k: "cái" if k == "phụ kiện" else "chiếc")
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["line_discount"] = (df["qty"] * df["unit_price"] - df["line_amount"]).round()
    return df
