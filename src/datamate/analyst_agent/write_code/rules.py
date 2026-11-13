"""Stage 6 of write_code (fixed code, no LLM): the plan's rules on stage 5's lines. explore_agent.md "Stage 6".

Agreed with the user (2026-11-12), in this order:
1. status + the order-level spread, on all rows: each line's word -> plan.statuses (a word not in the plan is a
   failure, cell null); with an order id, a BLANK order-level cell (ORDER_LEVEL: date, time, status, customer,
   channel, payment, seller) takes the order's first non-blank value (rulebook §8); a different value is never
   overwritten (a conflict, counted); an order with no status word on any line gets plan.blank_status; no order id:
   each row alone. Return lines are left out of the date spread (rule 2 sets their dates). No status column in
   the plan (agreed 2026-11-13; explore step 5e): every line gets plan.blank_status (None: status stays empty).
2. return lines: is_return_line = qty < 0 on every shop. When plan.return_lines: return_date = the return line's own
   date, its order_date / order_time = the earliest of the order's sale lines (null when the order has none), and
   every line of an order with a return line is 'returned' (overwrites rule 1, this column only, rulebook §1). When
   the plan says no returns, negative lines are flagged only (counted, nothing else changed).
3. line kind: 'service_fee' when the line's name is in plan.service_fee_names, else 'product'.
4. non-revenue rows (plan.non_revenue_names) dropped.
5. duplicate lines dropped: identical in every raw cell (stage 2's lines) to an earlier line with an order id
   (common.drop_duplicate_lines), after 4.
Names (rules 3, 4) = product_name, else product_code, compared by attach's value_key, as explore steps 7 and 6c.
Order-level conflicts are counted on the lines kept after 4 and 5 (Claude's choice, not discussed).
"""
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan
from src.datamate.analyst_agent.explore_agent.v2.cleaning_inputs.forward_fill import ORDER_LEVEL
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.parse import STATUS_RETURNED, is_blank

DATES = ("order_date", "order_time")
EXAMPLES = 3


@dataclass
class RulesReport:
    lines_in: int = 0
    lines_out: int = 0
    # 1. status
    status_column: bool = False
    unknown_words: list[tuple[str, int]] = field(default_factory=list)  # (word, lines): not in plan.statuses
    blank_status_lines: int = 0  # lines of orders with no word that got blank_status
    no_status_lines: int = 0  # lines of orders with no word while blank_status is None (left null)
    # 1. spread: column -> {"cells": n, "orders": n}; conflicts: column -> {"orders": n, "examples": [...]}
    spread: dict = field(default_factory=dict)
    conflicts: dict = field(default_factory=dict)
    # 2. return lines
    return_lines: int = 0
    return_orders: int = 0
    returned_overwritten: int = 0  # lines whose status said otherwise, set to returned
    only_return_orders: int = 0  # orders with no sale line: order_date left null
    flagged_only: int = 0  # negative lines on a shop the plan says has no returns
    no_order_returns: int = 0  # return lines without an order id: dates left as written
    # 3-5
    service_fee_lines: int = 0
    non_revenue_dropped: list[tuple[str, int]] = field(default_factory=list)  # (name, lines)
    duplicates_dropped: int = 0
    duplicate_examples: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Ruled:
    lines: pd.DataFrame  # stage 6 output: stage 5's columns + is_return_line, return_date, line_kind, rows dropped
    report: RulesReport


def _keys(names: list[str]) -> set[str]:
    return {k for k in (value_key(n) for n in names) if k}


def line_names(lines: pd.DataFrame) -> pd.Series:
    """A line's name as steps 7 / 6c name it: product_name, else product_code; as value_key."""
    name = lines["product_name"] if "product_name" in lines.columns else pd.Series(None, index=lines.index)
    if "product_code" in lines.columns:
        name = name.where(name.map(lambda v: not is_blank(v)), lines["product_code"])
    return name.map(value_key)


def map_status_words(raw: pd.Series, plan: SalesLinePlan, report: RulesReport) -> tuple[pd.Series, pd.Series]:
    """Each written word -> its status (exact stripped text first, then value_key). Returns (status, failed)."""
    exact = {w.raw.strip(): w.status for w in plan.statuses}
    by_key = {value_key(w.raw): w.status for w in plan.statuses}
    unknown: Counter = Counter()
    out, failed = [], []
    for v in raw:
        if is_blank(v):
            out.append(None)
            failed.append(False)
            continue
        s = str(v).strip()
        st = exact.get(s) or by_key.get(value_key(s))
        if st is None:
            unknown[s] += 1
        out.append(st)
        failed.append(st is None)
    report.unknown_words = unknown.most_common()
    return pd.Series(out, index=raw.index, dtype=object), pd.Series(failed, index=raw.index)


def spread(lines: pd.DataFrame, col: str, use: pd.Series, report: RulesReport) -> None:
    """Rule 1: within an order, a blank cell of `col` takes the order's first non-blank value (in place).
    `use`: the lines taking part (order id written, not a failed cell, not a return line for the dates)."""
    s = lines[col]
    oid = lines["order_id"]
    src = use & s.map(lambda v: not is_blank(v))
    if not src.any():
        return
    first = s[src].groupby(oid[src], sort=False).first()
    fill = use & s.map(is_blank) & oid.isin(first.index)
    if fill.any():
        lines.loc[fill, col] = oid[fill].map(first)
        report.spread[col] = {"cells": int(fill.sum()), "orders": int(oid[fill].nunique())}


def conflicts(lines: pd.DataFrame, report: RulesReport) -> None:
    """Orders with two different non-blank values in an order-level column (dates: sale lines only)."""
    if "order_id" not in lines.columns:
        return
    is_return = lines["is_return_line"].fillna(False).astype(bool)
    for col in [c for c in ORDER_LEVEL if c in lines.columns]:
        use = lines["order_id"].notna() & ~is_return if col in DATES else lines["order_id"].notna()
        keys = lines.loc[use, col].map(lambda v: value_key(str(v)) if not is_blank(v) else None)
        n = keys.groupby(lines.loc[use, "order_id"], sort=False).nunique()
        two = n[n > 1]
        if len(two):
            ex = []
            for o in two.index[:EXAMPLES]:
                vals = lines.loc[use & (lines["order_id"] == o), col].dropna().map(str).unique()[:3]
                ex.append(f"{o}: {list(vals)}")
            report.conflicts[col] = {"orders": len(two), "examples": ex}


def return_rules(lines: pd.DataFrame, plan: SalesLinePlan, report: RulesReport) -> None:
    """Rule 2 (in place)."""
    qty = pd.to_numeric(lines["qty"], errors="coerce") if "qty" in lines.columns \
        else pd.Series(float("nan"), index=lines.index)
    is_return = (qty < 0).fillna(False)
    lines["is_return_line"] = is_return
    lines["return_date"] = None
    report.return_lines = int(is_return.sum())
    if not is_return.any():
        return
    if not plan.return_lines:
        report.flagged_only = int(is_return.sum())
        return
    if "order_date" in lines.columns:
        lines.loc[is_return, "return_date"] = lines.loc[is_return, "order_date"]
    has_id = lines["order_id"].notna() if "order_id" in lines.columns else pd.Series(False, index=lines.index)
    report.no_order_returns = int((is_return & ~has_id).sum())
    if not has_id.any():
        return
    oid = lines["order_id"]
    ret_orders = set(oid[is_return & has_id])
    report.return_orders = len(ret_orders)
    sale = has_id & ~is_return
    target = is_return & has_id
    for col in DATES:
        if col in lines.columns:
            earliest = lines.loc[sale & lines[col].notna(), col].groupby(oid[sale & lines[col].notna()]).min()
            lines.loc[target, col] = oid[target].map(earliest)
    report.only_return_orders = len(ret_orders - set(oid[sale]))
    in_ret = has_id & oid.isin(ret_orders)
    if "status" in lines.columns:
        report.returned_overwritten = int((in_ret & (lines["status"] != STATUS_RETURNED)).sum())
    lines.loc[in_ret, "status"] = STATUS_RETURNED


def apply_rules(lines: pd.DataFrame, raw_lines: pd.DataFrame, plan: SalesLinePlan) -> Ruled:
    """Stage 6 on stage 5's lines; `raw_lines` = stage 2's raw lines (same index), for the duplicate rule."""
    report = RulesReport(lines_in=len(lines))
    lines = lines.copy()
    has_order = "order_id" in lines.columns and lines["order_id"].notna().any()

    # 1. status words
    failed = pd.Series(False, index=lines.index)
    if "status" in lines.columns:
        report.status_column = True
        lines["status"], failed = map_status_words(lines["status"], plan, report)
    qty = pd.to_numeric(lines["qty"], errors="coerce") if "qty" in lines.columns else None
    is_return = (qty < 0).fillna(False) if qty is not None else pd.Series(False, index=lines.index)

    # 1. the order-level spread (blanks only), on all rows
    if has_order:
        has_id = lines["order_id"].notna()
        for col in [c for c in ORDER_LEVEL if c in lines.columns]:
            use = has_id & ~failed if col == "status" else has_id
            if col in DATES:
                use = use & ~is_return
            spread(lines, col, use, report)

    # 1. orders with no status word -> blank_status (failed cells stay null)
    if "status" in lines.columns:
        empty = lines["status"].isna() & ~failed
        if plan.blank_status:
            lines.loc[empty, "status"] = plan.blank_status
            report.blank_status_lines = int(empty.sum())
        else:
            report.no_status_lines = int(empty.sum())
    elif plan.blank_status:  # no status column: step 5e's answer for an order with no written status
        lines["status"] = plan.blank_status
        report.blank_status_lines = len(lines)
    else:
        report.no_status_lines = len(lines)

    # 2. return lines
    return_rules(lines, plan, report)

    # 3. line kind
    names = line_names(lines)
    fee = names.isin(_keys(plan.service_fee_names))
    lines["line_kind"] = fee.map({True: "service_fee", False: "product"})
    report.service_fee_lines = int(fee.sum())

    # 4. non-revenue rows
    drop = names.isin(_keys(plan.non_revenue_names))
    if drop.any():
        report.non_revenue_dropped = Counter(names[drop]).most_common()
        lines = lines[~drop]

    # 5. duplicate lines (raw cells, lines with an order id)
    if has_order:
        raw = raw_lines.loc[lines.index]
        cells = [c for c in raw.columns if not c.startswith("source_")]
        dup = raw[cells].astype(str).duplicated() & lines["order_id"].notna()
        if dup.any():
            report.duplicates_dropped = int(dup.sum())
            report.duplicate_examples = [str(o) for o in lines.loc[dup, "order_id"].unique()[:EXAMPLES]]
            lines = lines[~dup]

    conflicts(lines, report)
    report.lines_out = len(lines)
    return Ruled(lines, report)
