"""Helpers shared by the reference converters."""
import csv
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import pandas as pd

from src.datamate.parse import clean_text, fold_accents, is_blank, norm_text, parse_date, parse_money, parse_qty

SUBTOTAL = re.compile(r"^\s*(tổng ngày|tổng tháng|tổng \d{1,2}/\d{1,2}|tổng cộng\s*$|tong cong\s*$|cộng ngày|cộng tháng|cộng\s*$"
                      r"|ghi chú\s*:)", re.IGNORECASE)


@dataclass
class Converted:
    lines: pd.DataFrame
    sources: list[str]
    data_end: str  # last day of data, from the README ("Dữ liệu có đến hết dd/mm/yyyy")
    notes: list[str] = field(default_factory=list)  # what the converter dropped or fixed, for the report
    brief: list[str] = field(default_factory=list)  # how the shop maps onto sales lines, shown to the agent


def read_xlsx(path: Path, header_row: int = 1, sheets: list[str] | None = None) -> pd.DataFrame:
    """All rows below the header of every sheet (or `sheets`), raw cell values kept as Python objects.
    Adds source_file / source_sheet / source_row; sheets are concatenated in workbook order."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    records = []
    for sn in sheets or wb.sheetnames:
        rows = wb[sn].iter_rows(values_only=True)
        for _ in range(header_row - 1):
            next(rows)
        header = [str(h).strip() if h is not None else f"_col{i}" for i, h in enumerate(next(rows))]
        for i, r in enumerate(rows, start=header_row + 1):
            if r and any(not is_blank(v) for v in r):
                rec = dict(zip(header, r))
                rec.update(source_file=path.name, source_sheet=sn, source_row=i)
                records.append(rec)
    wb.close()
    return pd.DataFrame.from_records(records).astype(object)


def read_csv(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        records = [d | {"source_file": path.name, "source_sheet": "", "source_row": i}
                   for i, d in enumerate(csv.DictReader(fh), start=2)]
    return pd.DataFrame.from_records(records).astype(object)


def read_sqlite(path: Path, table: str) -> pd.DataFrame:
    """One table of a sqlite file, values as stored (text stays text)."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        df = pd.read_sql_query(f'SELECT * FROM "{table}"', con)
    df["source_file"], df["source_sheet"], df["source_row"] = path.name, table, range(1, len(df) + 1)
    return df.astype(object)


def drop_subtotals(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """Drop subtotal / footer rows (`Tổng ngày dd/mm`, `Cộng`, `Tổng tháng`, `Ghi chú: ...`), in any column."""
    text_cols = [c for c in df.columns if not c.startswith("source_")]
    mask = df[text_cols].apply(lambda col: col.map(lambda v: isinstance(v, str) and bool(SUBTOTAL.match(v)))).any(axis=1)
    if mask.any():
        notes.append(f"dropped {int(mask.sum())} subtotal/footer rows")
    return df[~mask]


def drop_repeated_headers(df: pd.DataFrame, notes: list[str]) -> pd.DataFrame:
    """Drop rows that repeat the header inside the data (a cell equal to its own column name)."""
    cols = [c for c in df.columns if not c.startswith("source_") and not c.startswith("_col")]
    mask = pd.concat([df[c].map(lambda v, c=c: isinstance(v, str) and v.strip() == c) for c in cols], axis=1).sum(axis=1) >= 2
    if mask.any():
        notes.append(f"dropped {int(mask.sum())} repeated header rows")
    return df[~mask]


def forward_fill(df: pd.DataFrame, cols: list[str], notes: list[str]) -> pd.DataFrame:
    """Rulebook §8: a blank order-level cell on the following lines of an order repeats the first line.
    A line belongs to the order above it when its order id (`cols[0]`) is blank. Only those continuation lines
    are filled, and only from their own order's first line: a blank cell on a line that has an order id (e.g. a
    walk-in order with no customer) stays blank and is never copied from the previous order. Rows must be in
    file order."""
    df = df.copy()
    blank = df[cols[0]].map(is_blank)
    if blank.any():
        notes.append(f"forward-filled {', '.join(cols)} on {int(blank.sum())} lines")
        block = (~blank).cumsum()  # one block = an order's first line + the continuation lines below it
        for c in cols:
            first_line = df[c].where(~blank & ~df[c].map(is_blank)).groupby(block).transform("first")
            fill = blank & df[c].map(is_blank) & (block > 0)
            df.loc[fill, c] = first_line[fill]
    return df


def drop_duplicate_lines(df: pd.DataFrame, order_col: str, notes: list[str]) -> pd.DataFrame:
    """Rulebook §8: two lines of the same order identical in every cell are an export error, count once."""
    cells = [c for c in df.columns if not c.startswith("source_")]
    key = df[cells].astype(str)
    dup = key.duplicated() & df[order_col].notna()
    if dup.any():
        notes.append(f"dropped {int(dup.sum())} duplicate lines within an order")
    return df[~dup]


def clean_line_table(df: pd.DataFrame, order_col: str | None, order_cols: list[str], notes: list[str]) -> pd.DataFrame:
    """The fixed cleaning of a raw line table, in this order (each step does nothing when there is nothing to do):
    repeated header rows -> subtotal / footer rows -> forward fill -> duplicate lines within an order.
    Duplicates come after forward fill: before it, the continuation lines have no order id and are not compared.
    The inputs are decided by the explore_agent and come from its plan, not guessed here:
    `order_col`: the raw column mapped to order_id, None when the shop has no order id (each row then stands alone,
    §8, so there is nothing to de-duplicate). `order_cols`: the plan's forward_fill_columns (order id first);
    [] when every line carries its order's cells, and then nothing is filled."""
    df = drop_repeated_headers(df, notes)
    df = drop_subtotals(df, notes)
    if order_cols:
        df = forward_fill(df, order_cols, notes)
    if order_col is not None:
        df = drop_duplicate_lines(df, order_col, notes)
    return df


def readme_data_end(readme: Path) -> str:
    m = re.search(r"Dữ liệu có đến hết (\d{2})/(\d{2})/(\d{4})", readme.read_text(encoding="utf-8"))
    return f"{m[3]}-{m[2]}-{m[1]}"


def readme_conversions(readme: Path) -> pd.DataFrame:
    """The README list '  - <product>: 1 <big unit> = <factor> <base unit>' as a table."""
    rows = []
    for line in readme.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"\s+-\s+(.+?):\s*1\s+(.+?)\s*=\s*([\d.,]+)\s+(.+?)\s*$", line):
            rows.append({"product": m[1], "unit": m[2], "factor": float(m[3].replace(",", ".")), "base_unit": m[4]})
    return pd.DataFrame(rows, columns=["product", "unit", "factor", "base_unit"])


def conversion_lookup(conv: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(normalized product name, normalized big unit) -> number of base units."""
    return {(norm_text(r.product), norm_text(r.unit)): r.factor for r in conv.itertuples()}


def match_names(names: pd.Series, catalog_names: pd.Series) -> pd.Series:
    """Map each raw product name to the catalog name it refers to (rulebook §8: spellings of one name are one
    product): exact match after norm_text, else a unique match without diacritics. Unmatched -> NA."""
    exact = {norm_text(c): c for c in catalog_names}
    folded = defaultdict(set)
    for c in catalog_names:
        folded[fold_accents(c)].add(c)

    def one(name):
        if (k := norm_text(name)) in exact:
            return exact[k]
        hits = folded.get(fold_accents(name), set())
        return next(iter(hits)) if len(hits) == 1 else None

    return names.map(one)


def canonical_names(names: pd.Series) -> pd.Series:
    """Shops without a product list: map every spelling of a name to its most frequent spelling."""
    counts = names.map(clean_text).dropna().value_counts()
    best = {}
    for name in counts.index:  # most frequent first
        best.setdefault(fold_accents(name), name)
    return names.map(lambda v: best.get(fold_accents(v)))


def parse_qty_and_unit(qty_cells: pd.Series, unit_cells: pd.Series | None) -> tuple[pd.Series, pd.Series]:
    """Quantity cells may carry the unit ('3 chai'); the unit column wins when both exist."""
    parsed = qty_cells.map(parse_qty)
    qty = parsed.map(lambda p: p[0])
    unit_in_cell = parsed.map(lambda p: p[1])
    if unit_cells is None:
        return qty, unit_in_cell
    unit = unit_cells.map(norm_text)
    return qty, unit.where(unit.notna(), unit_in_cell)


def money(series: pd.Series) -> pd.Series:
    return series.map(parse_money)


def dates(series: pd.Series, default_years: pd.Series | None = None) -> pd.Series:
    if default_years is None:
        return series.map(parse_date)
    return pd.Series([parse_date(v, y) for v, y in zip(series, default_years)], index=series.index)


def implied_discount(qty: pd.Series, unit_price: pd.Series, line_amount: pd.Series) -> pd.Series:
    """Shops without a discount column: the line discount is what the amount is below qty × price."""
    return (qty * unit_price - line_amount).round()


def returned_orders_from_negative_lines(df: pd.DataFrame) -> pd.Series:
    """Rulebook §1: an order with any return line is a returned order as a whole."""
    return df.groupby("order_id")["is_return_line"].transform("any")
