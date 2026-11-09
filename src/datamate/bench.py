"""Compare a candidate sales_lines conversion with the hand-written reference of the same shop.

Each check returns a score in [0, 1] and the first differences, so a bad score points at the conversion step
that went wrong (e.g. revenue per month too high + more lines than the reference = subtotal rows kept).
Used by scripts/bench_analyst.py; works on DataFrames so checks can also be run in memory.
"""
from dataclasses import asdict, dataclass, field

import pandas as pd

from src.datamate.query_agent.query import VALID_STATUSES
from src.datamate.schema import SALES_LINES_COLUMNS, validate

N_EXAMPLES = 5  # differences shown per check
MONEY_TOL = 1  # VND: a money total within this of the reference counts as equal
QTY_TOL = 1e-6
DEBUG_COLUMNS = ("source_file", "source_sheet", "source_row")


@dataclass
class Check:
    name: str
    score: float  # 1 = identical to the reference
    summary: str
    examples: list[str] = field(default_factory=list)


def _ratio_score(cand: float, ref: float) -> float:
    """1 when equal, falling linearly to 0 at a 100% relative difference."""
    if ref == cand:
        return 1.0
    if ref == 0:
        return 0.0
    return max(0.0, 1.0 - abs(cand - ref) / abs(ref))


def _set_check(name: str, cand: set, ref: set) -> Check:
    missing, extra = sorted(ref - cand, key=str), sorted(cand - ref, key=str)
    union = ref | cand
    score = len(ref & cand) / len(union) if union else 1.0
    examples = [f"missing {v!r}" for v in missing[:N_EXAMPLES]] + [f"extra {v!r}" for v in extra[:N_EXAMPLES]]
    return Check(name, score, f"{len(ref)} in reference, {len(missing)} missing, {len(extra)} extra", examples)


def _series_check(name: str, cand: pd.Series, ref: pd.Series, tol: float, fmt=str) -> Check:
    """Compare two keyed series (e.g. revenue per month); score = share of keys whose values agree."""
    both = pd.concat({"ref": ref, "cand": cand}, axis=1).fillna(0)
    bad = both[(both["cand"] - both["ref"]).abs() > tol]
    score = 1 - len(bad) / len(both) if len(both) else 1.0
    worst = bad.assign(d=(bad["cand"] - bad["ref"]).abs()).sort_values("d", ascending=False)
    examples = [f"{k}: ref {fmt(r)}, got {fmt(c)} ({fmt(c - r)})"
                for k, r, c in zip(worst.index, worst["ref"], worst["cand"])][:N_EXAMPLES]
    return Check(name, score, f"{len(both) - len(bad)}/{len(both)} keys agree", examples)


def _money(x: float) -> str:
    return f"{x:,.0f}"


def _qty(x: float) -> str:
    return f"{x:,.3f}".rstrip("0").rstrip(".")


def _orders(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["order_id"].notna()].drop_duplicates("order_id")


def _month(df: pd.DataFrame) -> pd.Series:
    return df["order_date"].dt.strftime("%Y-%m")


def _revenue_lines(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["status"].isin(VALID_STATUSES)]


def compare(cand: pd.DataFrame, ref: pd.DataFrame, cand_manifest: dict, ref_manifest: dict) -> list[Check]:
    checks = []

    # 1. Shape
    checks.append(Check("line_count", _ratio_score(len(cand), len(ref)),
                        f"ref {len(ref):,}, got {len(cand):,} ({len(cand) - len(ref):+,})"))
    ref_orders, cand_orders = _orders(ref), _orders(cand)
    checks.append(_set_check("order_ids", set(cand_orders["order_id"]), set(ref_orders["order_id"])))

    # 2. Which columns the shop records (missing column -> unanswerable in the query_agent)
    checks.append(_set_check("columns_available", set(cand_manifest.get("columns_available", [])),
                             set(ref_manifest.get("columns_available", []))))
    checks.append(Check("data_end", float(cand_manifest.get("data_end") == ref_manifest.get("data_end")),
                        f"ref {ref_manifest.get('data_end')}, got {cand_manifest.get('data_end')}"))
    filled = {c: (int(cand[c].notna().sum()), int(ref[c].notna().sum()))
              for c in SALES_LINES_COLUMNS if c not in DEBUG_COLUMNS}
    differ = {c: v for c, v in filled.items() if v[0] != v[1]}
    checks.append(Check("column_fill", 1 - len(differ) / len(filled), f"{len(filled) - len(differ)}/{len(filled)} "
                        "columns have the same number of filled lines",
                        [f"{c}: ref {r:,}, got {g:,}" for c, (g, r) in differ.items()][:N_EXAMPLES]))

    # 3. Statuses: counts per order, and agreement order by order
    ref_st = ref_manifest.get("status_counts", {})
    cand_st = cand_manifest.get("status_counts", {})
    checks.append(_series_check("status_counts", pd.Series(cand_st, dtype=float), pd.Series(ref_st, dtype=float), 0,
                                fmt=lambda x: f"{x:,.0f}"))
    shared = ref_orders.set_index("order_id")["status"].to_frame("ref").join(
        cand_orders.set_index("order_id")["status"].rename("cand"), how="inner")
    wrong = shared[shared["ref"] != shared["cand"]]
    pairs = wrong.value_counts().head(N_EXAMPLES)
    checks.append(Check("order_status", 1 - len(wrong) / len(shared) if len(shared) else 1.0,
                        f"{len(shared) - len(wrong):,}/{len(shared):,} shared orders have the same status",
                        [f"ref {r} -> got {c}: {n} orders" for (r, c), n in pairs.items()]))

    # 4. Money and counts per month (the rulebook's revenue rows: completed + in delivery)
    rr, cr = _revenue_lines(ref), _revenue_lines(cand)
    checks.append(_series_check("revenue_by_month", cr.groupby(_month(cr))["line_amount"].sum(),
                                rr.groupby(_month(rr))["line_amount"].sum(), MONEY_TOL, _money))
    checks.append(_series_check("revenue_by_day", cr.groupby(cr["order_date"].dt.date)["line_amount"].sum(),
                                rr.groupby(rr["order_date"].dt.date)["line_amount"].sum(), MONEY_TOL, _money))
    checks.append(_series_check("orders_by_month", cand_orders.groupby(_month(cand_orders)).size(),
                                ref_orders.groupby(_month(ref_orders)).size(), 0, lambda x: f"{x:,.0f}"))

    # 5. Products
    checks.append(_set_check("product_keys", set(cand["product_key"].dropna()), set(ref["product_key"].dropna())))
    rp, cp = rr[rr["line_kind"] == "product"], cr[cr["line_kind"] == "product"]
    checks.append(_series_check("qty_base_by_product", cp.groupby("product_key")["qty_base"].sum(),
                                rp.groupby("product_key")["qty_base"].sum(), QTY_TOL, _qty))
    checks.append(_series_check("line_kinds", cand["line_kind"].value_counts(dropna=False).astype(float),
                                ref["line_kind"].value_counts(dropna=False).astype(float), 0, lambda x: f"{x:,.0f}"))
    checks.append(_set_check("product_groups", set(cand["product_group"].dropna()), set(ref["product_group"].dropna())))

    # 6. Customers
    checks.append(_set_check("customer_keys", set(cand["customer_key"].dropna()), set(ref["customer_key"].dropna())))

    # 7. The candidate's own consistency (schema.validate); the reference passes it
    problems = validate(cand)
    checks.append(Check("validate", float(not problems), f"{len(problems)} problems", problems[:N_EXAMPLES]))
    return checks


def shop_score(checks: list[Check]) -> float:
    return sum(c.score for c in checks) / len(checks)


def to_json(checks: list[Check]) -> list[dict]:
    return [asdict(c) for c in checks]
