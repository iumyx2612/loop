"""The sales_lines standard table: columns, types, validation and the per-shop manifest.

Column meanings are documented in problem.md ("sales_lines: columns found and schema").
"""
import json
from pathlib import Path

import pandas as pd

from src.datamate.parse import CHANNELS, PAYMENT_METHODS, STATUSES

SALES_LINES_COLUMNS = {
    "order_id": "string",
    "line_no": "Int64",
    "order_date": "datetime64[s]",
    "order_time": "datetime64[s]",
    "status": "string",
    "customer_key": "string",
    "customer_name": "string",
    "customer_group": "string",
    "channel": "string",
    "payment_method": "string",
    "seller": "string",
    "product_key": "string",
    "product_code": "string",
    "product_name": "string",
    "product_group": "string",
    "line_kind": "string",
    "is_return_line": "boolean",
    "unit": "string",
    "qty": "float64",
    "base_unit": "string",
    "unit_factor": "float64",
    "qty_base": "float64",
    "return_date": "datetime64[s]",
    "unit_price": "Int64",
    "line_discount": "Int64",
    "line_amount": "Int64",
    "cost_price": "Int64",
    "source_file": "string",
    "source_sheet": "string",
    "source_row": "Int64",
}
MONEY_COLUMNS = ("unit_price", "line_discount", "line_amount", "cost_price")
# Columns every converted shop must fill on every line
REQUIRED = ("order_date", "status", "product_key", "product_name", "line_kind", "is_return_line", "unit", "qty",
            "base_unit", "unit_factor", "qty_base", "unit_price", "line_discount", "line_amount")
LINE_KINDS = ("product", "service_fee")


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """Add missing columns as null, put columns in schema order and cast them to the schema types."""
    df = df.copy()
    for col in SALES_LINES_COLUMNS:
        if col not in df.columns:
            df[col] = None
    for col in MONEY_COLUMNS:
        df[col] = pd.to_numeric(df[col]).round()
    out = pd.DataFrame(index=df.index)
    for col, dtype in SALES_LINES_COLUMNS.items():
        if dtype.startswith("datetime"):
            out[col] = pd.to_datetime(df[col]).astype(dtype)
        else:
            out[col] = df[col].astype(dtype)
    return out.reset_index(drop=True)


def validate(df: pd.DataFrame) -> list[str]:
    """Consistency checks on a converted table; returns a list of problems (empty = all good)."""
    problems = []

    def check(mask, what):
        n = int(mask.sum())
        if n:
            problems.append(f"{n} lines: {what}")

    for col in REQUIRED:  # a column empty on every line is a shop that does not record it (manifest), not a bug
        if df[col].notna().any():
            check(df[col].isna(), f"{col} is empty")
    check(~df["status"].isin(STATUSES) & df["status"].notna(), "status outside the four standard values")
    check(~df["line_kind"].isin(LINE_KINDS) & df["line_kind"].notna(), "unknown line_kind")
    for col, dtype in SALES_LINES_COLUMNS.items():  # a blank cell stringified by mistake
        if dtype == "string":
            check(df[col].str.strip().str.lower().isin(["nan", "none", "<na>"]), f"{col} holds the text 'nan'/'None'")
    check(~df["channel"].isin(CHANNELS) & df["channel"].notna(), "unknown channel")
    check(~df["payment_method"].isin(PAYMENT_METHODS) & df["payment_method"].notna(), "unknown payment_method")
    check((df["qty_base"] - df["qty"] * df["unit_factor"]).abs() > 1e-6, "qty_base != qty × unit_factor")
    expected = df["qty"] * df["unit_price"] - df["line_discount"]
    check((expected - df["line_amount"]).abs() > 1, "line_amount != qty × unit_price − line_discount")
    check((df["line_discount"] < 0) & ~df["is_return_line"].fillna(False), "negative line_discount on a sale line")
    check(df["is_return_line"].fillna(False) & df["return_date"].isna() & df["order_time"].notna(), "return line without return_date")

    # Order-level columns must be the same on every line of an order
    with_order = df[df["order_id"].notna()]
    for col in ("order_date", "status", "customer_key", "channel", "payment_method", "seller"):
        n = int((with_order.groupby("order_id")[col].nunique(dropna=False) > 1).sum())
        if n:
            problems.append(f"{n} orders: more than one {col}")
    return problems


def manifest(df: pd.DataFrame, shop: str, sources: list[str], data_end: str, brief: list[str] | None = None) -> dict:
    """What the agent needs to know before querying: which columns this shop has, and the date range.
    `data_end` is the README's last day of data (may be after the last order)."""
    n_orders = int(df["order_id"].nunique())
    return {
        "shop": shop,
        "table": "sales_lines",
        "sources": sources,
        "rows": len(df),
        "orders": n_orders if n_orders else None,
        "date_min": str(df["order_date"].min().date()),
        "date_max": str(df["order_date"].max().date()),
        "data_end": data_end,
        "columns_available": [c for c in SALES_LINES_COLUMNS if df[c].notna().any()],
        "columns_missing": [c for c in SALES_LINES_COLUMNS if not df[c].notna().any()],
        "status_counts": {k: int(v) for k, v in df.drop_duplicates("order_id")["status"].value_counts().items()}
        if n_orders else {k: int(v) for k, v in df["status"].value_counts().items()},
        **({"brief": brief} if brief else {}),
    }


def write(df: pd.DataFrame, out_dir: Path, shop: str, sources: list[str], data_end: str, brief: list[str] | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_dir / "sales_lines.parquet", index=False)
    m = manifest(df, shop, sources, data_end, brief)
    (out_dir / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m
