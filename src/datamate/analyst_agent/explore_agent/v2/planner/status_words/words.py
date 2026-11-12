"""Step 5a / 5b (code, no LLM): where the status column is, the words it holds, and its leftover blanks.

5a: the status column is step 2's mapping (`status` on the line tables), else the first table that filled `status`
in step 3's snapshot (the field resolve is postponed: one passing candidate is enough, like report_missing).
5b: the distinct non-blank values over every table of that group, data rows only (the filter step's rows left
out: dev_002's 420 repeated headers would add 'Trạng thái đơn hàng' as a word), with how many rows hold each and
the first row holding it. Values are compared stripped, nothing else: 'hủy' and 'Hủy' are two words (plan_check
allows both, as long as they get the same status).
"""
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import line_tables_of
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.attach import shop_groups
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables.nodes import option_names
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.parse import is_blank


LEFTOVER_EXAMPLES = 2  # rows shown for the leftover blanks


@dataclass
class StatusColumn:
    tables: list[explore.Table]
    column: str
    where: str  # the table group's name, as in step 1
    source: str  # "step 2" (line tables) or "snapshot"
    order_col: str | None  # the order id column of those tables; None = each row stands alone


@dataclass
class Words:
    counts: Counter = field(default_factory=Counter)  # word -> rows holding it, in order of first appearance
    first: dict = field(default_factory=dict)  # word -> (table, row number) of its first row
    blank: int = 0  # data rows with a blank status cell
    rows: int = 0  # data rows
    # Leftover blanks (user, 2026-11-09): blank status on a row whose order id is filled (or on any row when there
    # is no order id). A blank on a continuation line (order id blank) repeats the order's first line (rulebook §8,
    # forward fill, step 6); a leftover blank is not explained by that, so it has a meaning of its own (in a notes
    # column: completed, dev_011 / dev_013), which the LLM reads from the rulebook (blank_status)
    leftover: int = 0
    # (table, row number) of the first LEFTOVER_EXAMPLES, each from a different order (2026-11-11: dev_013 writes the
    # order id on every line, and two lines of ORD000001 looked like an order's later lines to the 9B)
    leftover_rows: list = field(default_factory=list)
    leftover_orders: int = 0  # distinct order ids among the leftover blanks (0 without an order id column)


def status_column(state: WorkflowState) -> StatusColumn | None:
    """5a. None when neither step 2 nor step 3 found a status column. The order id column: step 2's order_id on the
    line tables; for a snapshot table, the column it attached by for order_id (attach's links)."""
    shop_dir = Path(state["shop_dir"])
    columns = {c.schema_column: c.raw_column for c in state.get("columns") or []}
    if "status" in columns:
        tables = line_tables_of(shop_dir, state["line_tables"], state["header_rows"], state.get("skipped_rows"))
        groups = [explore.TableGroup(tables)]
        return StatusColumn(tables, columns["status"], option_names(groups)[0], "step 2", columns.get("order_id"))
    snap = next((s for s in state.get("snapshots") or [] if "status" in s.get("filled", {})), None)
    if snap is None:
        return None
    g = shop_groups(state)[snap["table"]]
    links = next((a["links"] for a in state.get("attached") or [] if a["table"] == snap["table"]), [])
    order_col = next((k["column"] for k in links if k["anchor"] == "order_id"), None)
    return StatusColumn(g.tables, snap["filled"]["status"], snap["table"], "snapshot", order_col)


def status_words(col: StatusColumn) -> Words:
    """5b, and the leftover blanks."""
    out = Words()
    orders: set[str] = set()  # order ids of the leftover blanks
    for t in col.tables:
        j = explore.column_index(t, col.column)
        if j is None:
            continue
        k = explore.column_index(t, col.order_col) if col.order_col else None
        for i in t.data_numbers():
            r = t.rows[i - 1]
            if all(is_blank(v) for v in r):
                continue
            out.rows += 1
            v = r[j] if j < len(r) else None
            if is_blank(v):
                out.blank += 1
                if k is None or (k < len(r) and not is_blank(r[k])):  # not a continuation line
                    out.leftover += 1
                    order = None if k is None else str(r[k]).strip()
                    new_order = order is None or order not in orders
                    if order is not None:
                        orders.add(order)
                    if new_order and len(out.leftover_rows) < LEFTOVER_EXAMPLES:
                        out.leftover_rows.append((t, i))
                continue
            w = str(v).strip()
            out.counts[w] += 1
            out.first.setdefault(w, (t, i))
    out.leftover_orders = len(orders)
    return out


def where_text(t: explore.Table, i: int) -> str:
    return f"{label(t.file, t.sheet)} row {i}"
