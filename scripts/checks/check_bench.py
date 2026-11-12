"""Check that the analyst benchmark catches typical conversion mistakes, and points at the right checks.

Each case takes a reference conversion (data_std/), breaks it in memory the way a bad converter would,
rebuilds the manifest and runs bench.compare. The case passes when every expected check scores below 1.
Other checks that also drop are listed, to see how well a mistake is localized.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_bench.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

from src.datamate import bench, schema  # noqa: E402

STD = ROOT / "data_std" / "train"


def keep_subtotals(df):
    """One 'Tổng ngày' row per day, holding the day's total (biz_117 footers not dropped)."""
    day = df.groupby("order_date", as_index=False)["line_amount"].sum()
    rows = pd.DataFrame({"order_date": day["order_date"], "line_amount": day["line_amount"],
                         "product_key": "tổng ngày " + day["order_date"].dt.strftime("%d/%m"),
                         "product_name": "Tổng ngày", "status": "completed", "line_kind": "product",
                         "is_return_line": False, "unit": "", "qty": 0.0, "unit_factor": 1.0, "qty_base": 0.0,
                         "base_unit": "", "unit_price": 0, "line_discount": 0})
    return pd.concat([df, rows], ignore_index=True)


def no_forward_fill(df):
    """Order fields only on the first line of each order (biz_122 before forward fill)."""
    df = df.copy()
    later = df["order_id"].notna() & df.duplicated("order_id")
    for col in ("order_id", "order_date", "status", "customer_key", "customer_name"):
        df.loc[later, col] = None
    return df


def keep_duplicates(df):
    """2% of lines exported twice (biz_128 duplicates not dropped)."""
    return pd.concat([df, df.sample(frac=0.02, random_state=0)], ignore_index=True)


def cancelled_as_completed(df):
    """A cancellation word mapped to completed (biz_116 'Không giao được')."""
    df = df.copy()
    df.loc[df["status"] == "cancelled", "status"] = "completed"
    return df


def no_unit_conversion(df):
    """Big units not converted to the base unit (biz_108)."""
    df = df.copy()
    df["unit_factor"] = 1.0
    df["qty_base"] = df["qty"]
    return df


def drop_customer_group(df):
    """A column the shop records left unmapped (biz_108 customer group)."""
    return df.assign(customer_group=None)


CASES = [
    ("biz_117", keep_subtotals, {"line_count", "revenue_by_month", "revenue_by_day", "product_keys"}),
    ("biz_122", no_forward_fill, {"revenue_by_month", "column_fill", "validate"}),
    ("biz_128", keep_duplicates, {"line_count", "revenue_by_month", "qty_base_by_product"}),
    ("biz_116", cancelled_as_completed, {"status_counts", "order_status", "revenue_by_month"}),
    ("biz_108", no_unit_conversion, {"qty_base_by_product"}),
    ("biz_108", drop_customer_group, {"columns_available", "column_fill"}),
]


def main() -> None:
    cache = {}
    failures = 0
    for shop, breaker, expected in CASES:
        if shop not in cache:
            cache[shop] = (pd.read_parquet(STD / shop / "sales_lines.parquet"),
                           json.loads((STD / shop / "manifest.json").read_text(encoding="utf-8")))
        ref, ref_m = cache[shop]
        cand = schema.finalize(breaker(ref))
        cand_m = schema.manifest(cand, shop, ref_m["sources"], ref_m["data_end"], ref_m.get("brief"))
        checks = bench.compare(cand, ref, cand_m, ref_m)
        dropped = {c.name for c in checks if c.score < 1}
        ok = expected <= dropped
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {shop} {breaker.__name__}: score {bench.shop_score(checks):.3f}")
        if not ok:
            print(f"     not caught: {', '.join(sorted(expected - dropped))}")
        if dropped - expected:
            print(f"     also dropped: {', '.join(sorted(dropped - expected))}")
        for c in checks:
            if c.name in expected and c.examples:
                print(f"     {c.name}: {c.summary}; e.g. {c.examples[0]}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} cases caught")


if __name__ == "__main__":
    main()
