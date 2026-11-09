"""dev_012 (motorbike repair, owner's book): one sheet per month (a blank row above the header), footer rows; each
row is one ticket (rulebook §6 Sửa xe: one visit, warranty tickets included), with no ticket number, no customer
and one amount for the whole ticket. Mapped onto sales lines as one line per ticket:
- order_id: a ticket id made from the row's place in the book (two identical rows are two visits, §8);
- seller: the mechanic's full name from DanhSachTho.xlsx (the book writes the short name: Đạt, a Đạt, A.Đạt);
- customer_key: the licence plate (§6: a vehicle is identified by its plate), customer_name: the cell as written;
- line_amount: Tiền (0 on warranty tickets). The jobs (Làm gì) are free text and are not products here."""
import re
from pathlib import Path

import pandas as pd

from src.datamate.parse import STATUS_COMPLETED, clean_text, fold_accents
from src.datamate.reference.common import Converted, dates, drop_subtotals, money, read_xlsx, readme_data_end

PLATE = re.compile(r"(\d{2}[A-Z]{1,2}\d?-\d{3}\.\d{2}|\d{2}[A-Z]{1,2}\d?-\d{4,5})\s*$", re.IGNORECASE)


def mechanic_lookup(staff: pd.DataFrame) -> dict[str, str]:
    """Given name (last word of the full name, accents ignored) -> full name; given names must be unique."""
    by_given = {}
    for full in staff["Họ tên"].map(clean_text):
        given = fold_accents(full.split()[-1])
        if given in by_given:
            raise ValueError(f"two mechanics called {given!r}")
        by_given[given] = full
    return by_given


def short_name(v) -> str | None:
    """'A.Đức', 'a Đức', 'Đức' -> 'duc'."""
    s = clean_text(v)
    return None if s is None else fold_accents(re.sub(r"^(a|anh)\s*\.?\s+|^(a|anh)\.", "", s, flags=re.IGNORECASE))


def convert(shop_dir: Path) -> Converted:
    notes = []
    raw = read_xlsx(shop_dir / "SoSuaXe.xlsx", header_row=2)
    raw = drop_subtotals(raw, notes)

    mechanics = mechanic_lookup(read_xlsx(shop_dir / "DanhSachTho.xlsx"))
    seller = raw["Thợ"].map(lambda v: mechanics.get(short_name(v)))
    if seller.isna().any():
        raise ValueError(f"unknown mechanics: {sorted(raw.loc[seller.isna(), 'Thợ'].astype(str).unique())}")
    plate = raw["Xe"].map(lambda v: (m := PLATE.search(str(v).strip())) and m[1].upper())
    if plate.isna().any():
        raise ValueError(f"no plate in: {sorted(raw.loc[plate.isna(), 'Xe'].astype(str).unique())[:10]}")

    df = pd.DataFrame({
        "order_id": [f"{s}#{r}" for s, r in zip(raw["source_sheet"], raw["source_row"])],
        "order_date": dates(raw["Ngày"]),
        "status": STATUS_COMPLETED,
        "customer_key": plate,
        "customer_name": raw["Xe"].map(clean_text),
        "seller": seller,
        "line_amount": money(raw["Tiền"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    df["line_kind"] = "product"
    df["is_return_line"] = False
    df["line_discount"] = 0
    warranty = df["line_amount"] == 0
    notes.append(f"{int(warranty.sum())} warranty tickets (Tiền 0) kept: they count as visits (§6 Sửa xe)")

    brief = ["motorbike repair shop: one order = one ticket (phiếu, lượt xe), warranty tickets included with revenue 0",
             "seller = the mechanic (thợ), by full name; customer = the vehicle, identified by licence plate (biển số)",
             "the jobs done (Làm gì), the vehicle model and commissions are not in the sales lines: OUT OF SCOPE"]
    return Converted(df, ["SoSuaXe.xlsx", "DanhSachTho.xlsx"], readme_data_end(shop_dir / "README.md"), notes, brief)
