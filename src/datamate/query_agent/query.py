"""Measure-based queries over one shop's sales_lines table (the engine under the information_finder tool).

The caller names a *measure*; the measure decides which rows count (rulebook §1, §3, §5), so a caller cannot
forget e.g. that revenue drops cancelled and returned orders. The caller's filters and group_by only narrow
or split those rows. Results are small frames sorted by value desc, then code asc (§9 tie-break).
"""
import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate.parse import CHANNELS, PAYMENT_METHODS, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_IN_DELIVERY, norm_text

# §1: orders that count for revenue, order count, best sellers...: not cancelled, not returned; in delivery counts
VALID_STATUSES = (STATUS_COMPLETED, STATUS_IN_DELIVERY)

MEASURES = ("revenue", "order_count", "avg_order_value", "qty_base", "customer_count",
            "cancelled_count", "cancel_rate", "unit_price_base", "gross_profit")
# Columns a measure needs beyond order_date / status / line_amount (always present)
MEASURE_NEEDS = {"order_count": ["order_id"], "avg_order_value": ["order_id"], "customer_count": ["customer_key"],
                 "cancelled_count": ["order_id"], "cancel_rate": ["order_id"],
                 "qty_base": ["qty_base"], "unit_price_base": ["unit_price", "unit_factor"],
                 "gross_profit": ["qty", "cost_price"]}
# Measures on product lines only: Phí phục vụ is not a product (§3)
PRODUCT_ONLY = ("qty_base", "unit_price_base")
# Measures that sum or count, so a group missing from one period is 0 there (not unknown)
ADDITIVE = ("revenue", "order_count", "qty_base", "customer_count", "cancelled_count", "gross_profit")

FILTER_COLUMNS = {"product_keys": "product_key", "product_group": "product_group", "customer_keys": "customer_key",
                  "customer_group": "customer_group", "channel": "channel", "payment_method": "payment_method",
                  "seller": "seller", "shift": "order_time", "size": "product_name", "base_unit": "base_unit"}
GROUP_COLUMNS = {"product": "product_key", "product_group": "product_group", "customer": "customer_key",
                 "customer_group": "customer_group", "seller": "seller", "channel": "channel",
                 "payment_method": "payment_method", "order": "order_id", "day": "order_date", "week": "order_date",
                 "month": "order_date", "shift": "order_time"}
SHIFTS = ("morning", "afternoon")  # §2: by order creation time, 06:00–13:59 and 14:00–21:59


class QueryError(ValueError):
    """Invalid parameters; the message tells the caller what to fix."""


class NoData(Exception):
    """The shop's data cannot answer this (period outside the data, column not recorded): §7 unanswerable."""


@dataclass
class Shop:
    id: str
    lines: pd.DataFrame
    manifest: dict

    @classmethod
    def load(cls, shop_dir: Path) -> "Shop":
        manifest = json.loads((shop_dir / "manifest.json").read_text(encoding="utf-8"))
        return cls(manifest["shop"], pd.read_parquet(shop_dir / "sales_lines.parquet"), manifest)

    def has(self, col: str) -> bool:
        return col in self.manifest["columns_available"]

    @property
    def first_day(self) -> dt.date:
        return dt.date.fromisoformat(self.manifest["date_min"])

    @property
    def last_day(self) -> dt.date:
        return max(dt.date.fromisoformat(self.manifest["date_max"]), dt.date.fromisoformat(self.manifest["data_end"]))


@dataclass
class QueryResult:
    frame: pd.DataFrame  # ungrouped: one row [value]; grouped: [key, code, name, value]
    start: dt.date
    end: dt.date
    rows_used: str  # which rows the measure counted, in words
    notes: list[str] = field(default_factory=list)


def _shift(times: pd.Series) -> pd.Series:
    h = times.dt.hour
    return pd.Series(pd.NA, index=times.index, dtype="string").mask((h >= 6) & (h < 14), "morning").mask((h >= 14) & (h < 22), "afternoon")


def _check_available(shop: Shop, measure, filters, group_by, start, end) -> list[str]:
    for col in MEASURE_NEEDS.get(measure, []):
        if not shop.has(col):
            raise NoData(f"{measure} needs {col}, which this shop does not record")
    for name in filters:
        if not shop.has(FILTER_COLUMNS[name]):
            raise NoData(f"filter {name} needs {FILTER_COLUMNS[name]}, which this shop does not record")
    if group_by and not shop.has(GROUP_COLUMNS[group_by]):
        raise NoData(f"group_by {group_by} needs {GROUP_COLUMNS[group_by]}, which this shop does not record")
    first, last = shop.first_day, shop.last_day
    if end < first or (start is not None and start > last):
        raise NoData(f"the period {start or 'start'} → {end} is outside the shop's data ({first} → {last})")
    notes = []
    if start is not None and start < first:
        notes.append(f"data starts {first}: computed on {first} → {end}")
    return notes


def _apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    for name, v in filters.items():
        if name in ("product_keys", "customer_keys"):
            keys = [v] if isinstance(v, str) else list(v)
            df = df[df[FILTER_COLUMNS[name]].isin(keys)]
        elif name in ("product_group", "customer_group", "base_unit"):
            df = df[df[FILTER_COLUMNS[name]].map(norm_text) == norm_text(v)]
        elif name == "channel":
            if v not in CHANNELS:
                raise QueryError(f"channel must be one of {CHANNELS}, got {v!r}")
            df = df[df["channel"] == v]
        elif name == "payment_method":
            if v not in PAYMENT_METHODS:
                raise QueryError(f"payment_method must be one of {PAYMENT_METHODS}, got {v!r}")
            df = df[df["payment_method"] == v]
        elif name == "seller":
            df = df[df["seller"].str.strip().str.lower() == str(v).strip().lower()]
        elif name == "shift":
            if v not in SHIFTS:
                raise QueryError(f"shift must be one of {SHIFTS}, got {v!r}")
            df = df[_shift(df["order_time"]) == v]
        elif name == "size":  # §6 cà phê: name ends with (M) / (L), any case
            if str(v).upper() not in ("M", "L"):
                raise QueryError(f"size must be 'M' or 'L', got {v!r}")
            df = df[df["product_name"].str.strip().str.upper().str.endswith(f"({str(v).upper()})")]
        else:
            raise QueryError(f"unknown filter {name!r}; expected one of {', '.join(FILTER_COLUMNS)}")
    return df


def _group_keys(df: pd.DataFrame, group_by: str | None) -> pd.Series:
    if group_by is None:
        return pd.Series("all", index=df.index, dtype="string")
    if group_by == "day":
        return df["order_date"].dt.strftime("%Y-%m-%d")
    if group_by == "week":  # weeks start Monday (§2); key = the Monday
        return (df["order_date"] - pd.to_timedelta(df["order_date"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    if group_by == "month":
        return df["order_date"].dt.strftime("%Y-%m")
    if group_by == "shift":
        return _shift(df["order_time"])
    return df[GROUP_COLUMNS[group_by]].astype("string")


def _half_up(x: float) -> float:
    return math.floor(x + 0.5) if x == x else x


def _aggregate(df: pd.DataFrame, keys: pd.Series, measure: str) -> pd.Series:
    g = df.groupby(keys, dropna=True)
    if measure == "revenue":
        return g["line_amount"].sum().astype(float)
    if measure == "qty_base":
        return g["qty_base"].sum()
    if measure == "order_count":
        return g["order_id"].nunique().astype(float)
    if measure == "customer_count":  # walk-in orders have no customer_key and are not customers (§3)
        return g["customer_key"].nunique().astype(float)
    if measure == "cancelled_count":
        return g["order_id"].nunique().astype(float)
    if measure == "avg_order_value":  # money: rounded to the đồng, half up (§3)
        return (g["line_amount"].sum() / g["order_id"].nunique()).map(_half_up)
    if measure == "gross_profit":  # §5: revenue − Σ qty × cost price on the sale line (per sale unit)
        # Cost is multiplied per line before summing: each line has its own cost and unit. A service-fee line
        # sells no goods, so its cost is 0 (run_query has already refused product lines without a cost).
        cost = (df["qty"] * df["cost_price"]).where(df["line_kind"] == "product", 0).astype(float)
        return (df["line_amount"].astype(float) - cost).groupby(keys, dropna=True).sum().map(_half_up)
    if measure == "cancel_rate":  # §1: cancelled orders among all orders placed in the period; 2 decimals
        all_orders = g["order_id"].nunique()
        cancelled = df[df["status"] == STATUS_CANCELLED].groupby(keys[df["status"] == STATUS_CANCELLED])["order_id"].nunique()
        return (cancelled.reindex(all_orders.index, fill_value=0) / all_orders * 100).round(2)
    raise QueryError(f"unknown measure {measure!r}; expected one of {', '.join(MEASURES)}")


def _labels(df: pd.DataFrame, keys: pd.Series, group_by: str) -> pd.DataFrame:
    """code and display name per group key: the most frequent spelling as written in the data."""
    if group_by == "product":
        code, name = df["product_code"], df["product_name"]
    elif group_by == "customer":
        code, name = pd.Series(pd.NA, index=df.index, dtype="string"), df["customer_name"]
    else:
        return pd.DataFrame({"code": pd.NA, "name": keys.dropna().unique()}, index=keys.dropna().unique())
    t = pd.DataFrame({"key": keys, "code": code, "name": name}).dropna(subset=["key"])
    return t.groupby("key").agg(code=("code", "first"), name=("name", lambda s: s.mode().iat[0] if s.notna().any() else pd.NA))


def _sorted(frame: pd.DataFrame) -> pd.DataFrame:
    """§9: value desc; ties by code asc, no code → by key (the name, or the date for time groups)."""
    tie = frame["code"].fillna(frame["key"]).astype(str)
    order = sorted(range(len(frame)), key=lambda i: (-frame["value"].iat[i] if frame["value"].iat[i] == frame["value"].iat[i]
                                                     else math.inf, tie.iat[i]))
    return frame.iloc[order].reset_index(drop=True)


def run_query(shop: Shop, measure: str, start: dt.date | None, end: dt.date,
              filters: dict | None = None, group_by: str | None = None) -> QueryResult:
    """One measure over order_date in [start, end] (start None = from the first day of data)."""
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
    if measure not in MEASURES:
        raise QueryError(f"unknown measure {measure!r}; expected one of {', '.join(MEASURES)}")
    for name in filters:
        if name not in FILTER_COLUMNS:
            raise QueryError(f"unknown filter {name!r}; expected one of {', '.join(FILTER_COLUMNS)}")
    if group_by is not None and group_by not in GROUP_COLUMNS:
        raise QueryError(f"unknown group_by {group_by!r}; expected one of {', '.join(GROUP_COLUMNS)}")
    notes = _check_available(shop, measure, filters, group_by, start, end)

    df = shop.lines
    in_window = df["order_date"] <= pd.Timestamp(end)
    if start is not None:
        in_window &= df["order_date"] >= pd.Timestamp(start)
    df = df[in_window]
    # The measure's rows
    if measure in ("cancel_rate", "unit_price_base"):
        rows_used = "orders of every status"
    elif measure == "cancelled_count":
        df, rows_used = df[df["status"] == STATUS_CANCELLED], "cancelled orders"
    else:
        df, rows_used = df[df["status"].isin(VALID_STATUSES)], "orders not cancelled and not returned"
    if measure in PRODUCT_ONLY or group_by == "product":
        df = df[df["line_kind"] == "product"]
        rows_used += ", product lines only (no service fee)"
    df = _apply_filters(df, filters)
    if measure == "gross_profit":  # a product line without its cost would silently count as pure profit
        n = int((df["cost_price"].isna() & (df["line_kind"] == "product")).sum())
        if n:
            raise NoData(f"gross_profit: {n} sold product lines in the period have no cost price")
    keys = _group_keys(df, group_by)

    if measure == "unit_price_base":  # §5 "giá bán X ngày D": price per base unit before line discount
        price = (df["unit_price"] / df["unit_factor"]).round(2)
        t = pd.DataFrame({"key": keys, "value": price}).dropna(subset=["key"])
        frame = t.groupby(["key", "value"]).size().rename("n_lines").reset_index()
    else:
        frame = _aggregate(df, keys, measure).rename("value").rename_axis("key").reset_index()
    if group_by is None:
        if frame.empty:
            frame = pd.DataFrame({"value": [0.0 if measure in ADDITIVE else math.nan]})
            if measure not in ADDITIVE:
                notes.append("no orders in the period")
        return QueryResult(frame.drop(columns="key", errors="ignore"), start or shop.first_day, end, rows_used, notes)
    labels = _labels(df, keys, group_by)
    frame = frame.join(labels, on="key")[["key", "code", "name", "value"] + (["n_lines"] if "n_lines" in frame else [])]
    return QueryResult(_sorted(frame), start or shop.first_day, end, rows_used, notes)


def compare(cur: QueryResult, prev: QueryResult, measure: str) -> pd.DataFrame:
    """Line up a period with the one it is compared with. §1: diff = asked − compared (negative if lower);
    pct_change against the compared period, 2 decimals; undefined (NaN) when the compared value is 0."""
    if measure == "unit_price_base":
        raise QueryError("compare is not defined for unit_price_base; query the two days separately")
    a, b = cur.frame, prev.frame.rename(columns={"value": "value_prev"})
    if "key" in a:
        out = a.merge(b[["key", "value_prev"]], on="key", how="outer")
        missing = out["code"].isna() & out["name"].isna()
        out.loc[missing, ["code", "name"]] = b.set_index("key").loc[out.loc[missing, "key"], ["code", "name"]].values
    else:
        out = pd.concat([a, b[["value_prev"]]], axis=1)
    if measure in ADDITIVE:
        out[["value", "value_prev"]] = out[["value", "value_prev"]].fillna(0)
    out["diff"] = out["value"] - out["value_prev"]
    out["pct_change"] = (out["diff"] / out["value_prev"].where(out["value_prev"] != 0) * 100).round(2)
    return _sorted(out) if "key" in out else out
