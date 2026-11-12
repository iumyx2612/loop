"""How much the per-order spread of order-level cells would change (write_code stage 6 proposal, 2026-11-12). No LLM.

Rulebook §8: a blank cell on the later lines of an order repeats the order's value ("khách, ghi chú, trạng thái của
đơn áp cho mọi dòng của đơn"). Stage 2's forward fill only fills lines whose order id is blank; this check measures
the other case: the order id is written, but an order-level cell is blank on some lines of an order that has a value
on another line.

Per shop (plan from a saved explore run, stages 1-5 of write_code) and per ORDER_LEVEL column on the lines:
- would fill: cells blank on a line whose order has a non-blank value on another line (return lines left out for
  order_date / order_time: they carry the return's own date, rule 2);
- conflicts: orders with two different non-blank values (sale lines only for the dates);
- vs reference (where data_std/<split>/<shop>/sales_lines.parquet exists, lines matched on source_*): of the cells
  the spread would fill, how many equal the reference's value, and how many the reference leaves blank.
status is compared after mapping the raw word through the plan's statuses (blank -> blank_status).

Usage:
  .venv/Scripts/python.exe -I scripts/checks/check_order_spread.py runs/explore/v2/9B/train/step_9_conversion
      runs/explore/v2/9B/public_test/step_9_conversion
"""
import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "write_code"))
sys.path.insert(0, str(ROOT / "scripts" / "explore"))
sys.path.insert(0, str(ROOT))

from run_stages_1_4 import plan_of  # noqa: E402
from run_stage_5 import KEYS, same  # noqa: E402

import pandas as pd  # noqa: E402

import v2_runner  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.forward_fill import ORDER_LEVEL  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.write_code import KINDS, apply_stages_1_4, normalize  # noqa: E402
from src.datamate.parse import is_blank, norm_text  # noqa: E402

DATES = ("order_date", "order_time")


def status_of(plan):
    exact = {w.raw.strip(): w.status for w in plan.statuses}
    by_norm = {norm_text(w.raw): w.status for w in plan.statuses}

    def f(v):
        if is_blank(v):
            return plan.blank_status
        s = str(v).strip()
        return exact.get(s) or by_norm.get(norm_text(s))
    return f


def value_key(v):
    if is_blank(v):
        return None
    if isinstance(v, pd.Timestamp) or hasattr(v, "isoformat"):
        return str(pd.Timestamp(v))
    return norm_text(str(v)) if isinstance(v, str) else repr(v)


def check_shop(split: str, shop: str, state: dict) -> None:
    print(f"\n## {shop}")
    plan = plan_of(state)
    if plan is None:
        print("  no plan in the saved state")
        return
    try:
        applied = apply_stages_1_4(Path(state["shop_dir"]), plan)
        lines = normalize(applied.lines, plan).lines
    except Exception as e:  # noqa: BLE001
        print(f"  FAILED: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)
        return
    if "order_id" not in lines.columns or lines["order_id"].isna().all():
        print("  no order id: each row stands alone, nothing to spread")
        return
    cols = [c for c in ORDER_LEVEL if c in lines.columns]
    if "status" in cols:
        lines["status"] = lines["status"].map(status_of(plan))  # blank -> blank_status, as the line-by-line rule
        raw_status = applied.lines["status"]
    is_return = pd.to_numeric(lines["qty"], errors="coerce") < 0 if "qty" in lines.columns else \
        pd.Series(False, index=lines.index)
    has_id = lines["order_id"].notna()
    ref_path = v2_runner.ROOT / "data_std" / split / shop / "sales_lines.parquet"
    ref = None
    if ref_path.exists():
        ref = pd.read_parquet(ref_path)
        ref["source_file"] = ref["source_file"].astype(str)
        ref["source_sheet"] = ref["source_sheet"].fillna("").astype(str)
        ref["source_row"] = pd.to_numeric(ref["source_row"]).astype("Int64")
        sqlite = ref["source_file"].str.lower().str.endswith(".sqlite")
        ref.loc[sqlite, "source_row"] = ref.loc[sqlite, "source_row"] + 1
    print(f"  {len(lines):,} lines, {lines.loc[has_id, 'order_id'].nunique():,} orders; order-level columns: "
          f"{', '.join(cols) or '-'}")
    for c in cols:
        use = has_id & ~is_return if c in DATES else has_id
        # status: a blank raw cell is what the spread fills (blank_status already put on it by the line rule)
        blank = (raw_status.map(is_blank) if c == "status" else lines[c].isna()) & use
        filled = use & ~blank
        keys = lines.loc[filled, c].map(value_key)
        first = lines.loc[filled, c].groupby(lines.loc[filled, "order_id"]).first()
        n_vals = keys.groupby(lines.loc[filled, "order_id"]).nunique()
        conflicts = n_vals[n_vals > 1]
        would = blank & lines["order_id"].isin(first.index)
        line = f"    {c:<15} would fill {int(would.sum()):>6,} cells in {lines.loc[would, 'order_id'].nunique():>5,} orders"
        line += f" | conflicts {len(conflicts):>4,} orders"
        if len(conflicts):
            o = conflicts.index[0]
            vals = lines.loc[filled & (lines["order_id"] == o), c].map(value_key).unique()[:3]
            line += f" (e.g. {o}: {list(vals)})"
        print(line)
        if would.any():
            ex = lines.loc[would].head(2)
            for r in ex.itertuples():
                print(f"        e.g. {r.source_file} [{r.source_sheet}] row {r.source_row}: order {r.order_id}, "
                      f"now {getattr(r, c)!r} -> {first.get(r.order_id)!r}")
        if ref is not None and would.any() and c in ref.columns:
            ours = lines.loc[would, KEYS].copy()
            ours["source_file"] = ours["source_file"].astype(str)
            ours["source_sheet"] = ours["source_sheet"].fillna("").astype(str)
            ours["source_row"] = pd.to_numeric(ours["source_row"]).astype("Int64")
            ours["spread"] = lines.loc[would, "order_id"].map(first).values
            ours["now"] = lines.loc[would, c].values
            m = ours.merge(ref[KEYS + [c]], on=KEYS, how="inner")
            if len(m):
                kind = KINDS.get(c, "label")
                kind = kind if kind in ("date", "datetime") else "label"
                if c in ("customer_name", "seller", "status", "channel", "payment_method", "customer_code"):
                    a, b = m["spread"].map(value_key), m[c].map(value_key)
                    spread_ok = (a == b) | (a.isna() & b.isna())
                    now_ok = (m["now"].map(value_key) == b) | (m["now"].isna() & b.isna())
                else:
                    spread_ok = same(kind, m["spread"], m[c])
                    now_ok = same(kind, m["now"], m[c])
                print(f"        vs reference ({len(m):,} of these lines matched): spread = ref on {int(spread_ok.sum()):,}, "
                      f"line as now = ref on {int(now_ok.sum()):,}, ref blank on {int(m[c].isna().sum()):,}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("paths", nargs="+")
    p.add_argument("--shops", nargs="*", default=None)
    args = p.parse_args()
    for split, shop, state in v2_runner.load_saved(args.paths, args.shops):
        check_shop(split, shop, state)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
