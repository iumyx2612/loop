"""Step 6c (code, no LLM): count the duplicate lines within an order (agreed with the user, 2026-11-10).

Nothing to plan: the converter always drops them (rulebook §8: two lines of the same order identical in every cell
are an export error, counted once; two identical lines of different orders are two sales). This count is for
information only (a note in the plan, the trace).

Same rule as the references' common.drop_duplicate_lines, run after forward fill as they do (before it, the
continuation lines have no order id): the line tables' data rows are filled with 6a's forward_fill_columns (a
blank cell of a continuation line takes its order's first-line value), then a row identical in every cell to an
earlier row is a duplicate. The order id is one of the cells, so only rows of one order can match. Rows across the
group's tables are compared too (the converter concatenates them). No order id mapped → nothing counted (§8: a table
not written by order counts both). Rows above the first order id keep their blank order id and are not counted.

Cells are compared as the agent sees them (explore._cell: text in quotes, so '5' and 5 differ), like the
reference's astype(str).

Not-revenue rows left out (agreed with the user, 2026-11-11): rows whose product is one of step 7's non_revenue_names
(dev_013's Voucher / Phí ship / Dùng n điểm) are dropped by the converter anyway, so they are never counted nor
remembered as an earlier line (as the dev_013 reference drops them before de-duplicating). A row's name is its
product_name cell, else (code-only shops) the products table's name for its product_code cell, else the code itself
(step 7's display name); compared by attach.value_key. Service-fee rows stay in: they are revenue and kept.
"""
from dataclasses import dataclass, field

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.parse import is_blank

EXAMPLES = 3  # duplicates shown


@dataclass
class Duplicates:
    order_col: str | None
    count: int = 0  # duplicate rows (each beyond the first copy)
    orders: int = 0  # orders holding at least one
    examples: list = field(default_factory=list)  # "order X: <table> row N = <table> row M"
    not_revenue_rows: int = 0  # rows left out as step 7's not-revenue names
    skipped: str | None = None


def row_name(r: list, j_name: int | None, j_code: int | None, names_by_code: dict) -> str | None:
    """value_key of the row's product name, as step 7 names it (see the module docstring)."""
    if j_name is not None and j_name < len(r) and not is_blank(r[j_name]):
        return value_key(r[j_name])
    if j_code is not None and j_code < len(r) and not is_blank(r[j_code]):
        code = value_key(r[j_code])
        return value_key(names_by_code[code]) if code in names_by_code else code
    return None


def duplicates(tables: list[explore.Table], order_col: str | None, ff_columns: list[str],
               name_col: str | None = None, code_col: str | None = None, not_revenue: list[str] = (),
               names_by_code: dict | None = None) -> Duplicates:
    """6c on the line tables, with step 2's order id column and 6a's forward_fill_columns. Rows named in
    `not_revenue` (step 7's non_revenue_names; name from step 2's product_name / product_code columns, `names_by_code` =
    {value_key(code): products-table name}) are left out."""
    out = Duplicates(order_col)
    if order_col is None:
        out.skipped = "no order id mapped: each row counts on its own (§8), nothing to drop"
        return out
    drop = {value_key(n) for n in not_revenue} - {None}
    names_by_code = names_by_code or {}
    seen: dict[tuple, str] = {}  # filled row key -> where it was first seen
    orders: set = set()
    first: dict[str, object] = {}  # forward-fill column -> the current order's first-line cell
    for t in tables:
        k = explore.column_index(t, order_col)
        j_name = explore.column_index(t, name_col) if name_col else None
        j_code = explore.column_index(t, code_col) if code_col else None
        fill = {c: j for c in ff_columns if (j := explore.column_index(t, c)) is not None}
        width = len(t.columns)
        for i in t.data_numbers():
            r = list(t.rows[i - 1]) + [None] * max(0, width - len(t.rows[i - 1]))
            if all(is_blank(v) for v in r):
                continue
            if k is not None and not is_blank(r[k]):
                first = {c: r[j] for c, j in fill.items()}
            else:
                for c, j in fill.items():  # a continuation line: blank cells take the first line's value
                    if is_blank(r[j]):
                        r[j] = first.get(c)
            oid = r[k] if k is not None else None
            if is_blank(oid):
                continue  # above the first order id
            if drop and row_name(r, j_name, j_code, names_by_code) in drop:
                out.not_revenue_rows += 1
                continue  # dropped by the converter: never a duplicate nor an earlier line
            key =tuple(explore._cell(v) for v in r)
            where = f"{label(t.file, t.sheet)} row {i}"
            if key in seen:
                out.count += 1
                orders.add(explore._cell(oid))
                if len(out.examples) < EXAMPLES:
                    out.examples.append(f"order {explore._cell(oid)}: {seen[key]} = {where}")
            else:
                seen[key] = where
    out.orders = len(orders)
    return out


def note(d: Duplicates) -> str | None:
    """The plan note, None when there is nothing to say."""
    if not d.count:
        return None
    return (f"{d.count} line(s) in {d.orders} order(s) repeat an earlier line of the same order in every cell "
            "(after forward fill): an export error, count once (rulebook §8)")
