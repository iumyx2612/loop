"""Step 6a (code, no LLM): which line-table columns the converter forward fills (agreed with the user, 2026-11-10).

Continuation line = a data row of the line tables (the filter step's rows left out) whose order id is blank.
Rulebook §8: a blank cell on the following lines of a group is the group's first-line value (customer, notes,
status), never missing data, walk-in or completed; so a blank order id is never a stand-alone sale, and no check
looks for one.

forward_fill_columns = the order id + every ORDER_LEVEL column step 2 mapped on the line table that is blank on at
least one continuation line. No threshold (user): forward fill only fills blank cells of continuation lines from
their own order's first line (common.forward_fill) and never overwrites a filled cell, so a column sometimes
rewritten on continuation lines is still right. A column never blank on a continuation line is left out only
because there is nothing to fill (plan_check: "never blank ... remove it"). [] when no order id is mapped or it is
never blank. Fields from an attached orders table come through the join, not forward fill.

The tables of the group are read in order as one sequence (month sheets, split files): a continuation line at the
top of a sheet belongs to the last order of the sheet before, as when the converter concatenates them. Lines above
the first order id have no first line to fill from: counted as `orphans`.

Check (trace warning, no fix): after filling, an order id whose lines hold two different values in a filled
column (values compared without accents, case or extra spaces). It is the one case where the two reference styles
differ (common.forward_fill keeps a continuation line's own value; phone_shop.per_order takes the order's first
one) and the rulebook says nothing. Return lines (6b's rows) are left out (user, 2026-11-10): a return line is its
own line carrying the return's date (dev_004, biz_117), which the converter turns into return_date; an order that
differs only there is counted as `return_only`, not warned about.
"""
from dataclasses import dataclass, field

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.parse import is_blank

# the sales_lines fields that belong to the order, not the line: the same on every line of an order
ORDER_LEVEL = ["order_date", "order_time", "status", "customer_name", "customer_code", "customer_group", "channel",
               "payment_method", "seller"]
CONFLICT_EXAMPLES = 3  # orders shown per column with two values


@dataclass
class ForwardFill:
    order_col: str | None
    columns: list[str] = field(default_factory=list)  # the plan's forward_fill_columns, order id first
    rows: int = 0  # data rows of the line tables
    continuation: int = 0  # of them, rows with a blank order id
    orphans: int = 0  # continuation rows above the first order id: nothing to fill from
    blank_on_continuation: dict = field(default_factory=dict)  # raw column -> continuation rows blank in it
    fields: dict = field(default_factory=dict)  # raw column -> the order-level fields mapped on it
    left_out: dict = field(default_factory=dict)  # raw column -> why it is not filled
    conflicts: dict = field(default_factory=dict)  # raw column -> {"orders": n, "examples": [...]}, sale lines only
    return_only: dict = field(default_factory=dict)  # raw column -> orders whose two values come from return lines
    skipped: str | None = None  # why there is nothing to fill


def order_level_columns(columns: dict[str, str]) -> dict[str, list[str]]:
    """raw column -> the ORDER_LEVEL fields step 2 mapped on it, in ORDER_LEVEL order (date + time can share one)."""
    out: dict[str, list[str]] = {}
    for f in ORDER_LEVEL:
        if f in columns:
            out.setdefault(columns[f], []).append(f)
    return out


def _cell(r: tuple, j: int | None):
    return r[j] if j is not None and j < len(r) else None


def forward_fill(tables: list[explore.Table], columns: dict[str, str],
                 return_rows: set | None = None) -> ForwardFill:
    """6a on the line tables, with step 2's mapping {field: raw column} and 6b's return lines
    {(explore.table_key, row number)}, which the per-order check leaves out."""
    return_rows = return_rows or set()
    order_col = columns.get("order_id")
    out = ForwardFill(order_col)
    if order_col is None:
        out.skipped = "no order id mapped: each row stands alone, nothing to fill"
        return out
    candidates = {c: fs for c, fs in order_level_columns(columns).items() if c != order_col}
    out.fields = candidates
    out.blank_on_continuation = {c: 0 for c in candidates}

    # one pass in file order: fill as the converter would, and collect each order id's values per column
    seen: dict[str, dict[str, dict]] = {c: {} for c in candidates}  # column -> order key -> {value key: (cell, where)}
    seen_all: dict[str, dict[str, set]] = {c: {} for c in candidates}  # the same keys with the return lines
    current: str | None = None  # the order id of the last first line
    first: dict[str, object] = {}  # column -> the current order's first-line cell
    for t in tables:
        tkey = explore.table_key(t.file, t.sheet)
        k = explore.column_index(t, order_col)
        idx = {c: explore.column_index(t, c) for c in candidates}
        for i in t.data_numbers():
            r = t.rows[i - 1]
            if all(is_blank(v) for v in r):
                continue
            out.rows += 1
            oid = _cell(r, k)
            if is_blank(oid):
                out.continuation += 1
                if current is None:
                    out.orphans += 1
                for c, j in idx.items():
                    if is_blank(_cell(r, j)):
                        out.blank_on_continuation[c] += 1
            else:
                current = value_key(oid)
                first = {c: _cell(r, j) for c, j in idx.items()}
            if current is None:
                continue
            is_return = (tkey, i) in return_rows
            for c, j in idx.items():
                v = _cell(r, j)
                if is_blank(v) and is_blank(oid):
                    v = first.get(c)  # the fill: a blank continuation cell takes the first line's value
                key = value_key(v)
                if key is None:
                    continue
                seen_all[c].setdefault(current, set()).add(key)
                if not is_return:
                    seen[c].setdefault(current, {}).setdefault(
                        key, (explore._cell(v), f"{label(t.file, t.sheet)} row {i}"))

    if out.continuation == 0:
        out.skipped = f"{order_col!r} is never blank: every line carries its order's cells, nothing to fill"
        return out
    out.columns = [order_col]
    for c in candidates:
        if out.blank_on_continuation[c]:
            out.columns.append(c)
        else:
            out.left_out[c] = "never blank on a continuation line: nothing to fill"
    for c in out.columns[1:]:
        two = [(o, vals) for o, vals in seen[c].items() if len(vals) > 1]
        if two:
            out.conflicts[c] = {"orders": len(two), "examples": [
                {"order": o, "values": [f"{cell} ({where})" for cell, where in vals.values()]}
                for o, vals in two[:CONFLICT_EXAMPLES]]}
        conflicting = {o for o, _ in two}
        n = sum(len(keys) > 1 and o not in conflicting for o, keys in seen_all[c].items())
        if n:
            out.return_only[c] = n
    return out


def problems(ff: ForwardFill) -> list[str]:
    """The trace warnings: no fix, a shop showing one is looked at by hand."""
    out = []
    if ff.orphans:
        out.append(f"{ff.orphans} line(s) with a blank {ff.order_col!r} above the first order: no first line to fill "
                   "from")
    for c, x in ff.conflicts.items():
        out.append(f"{c!r}: {x['orders']} order(s) hold two different values after forward fill, e.g. "
                   + "; ".join(f"{e['order']}: " + " vs ".join(e["values"]) for e in x["examples"]))
    return out
