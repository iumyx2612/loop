"""Step 6b (code, no LLM): does the shop write returns as negative-quantity lines (agreed with the user, 2026-11-10).

Return line = a data row of the line tables whose qty (step 2's `qty` column, read by parse.parse_qty: '-2 kg' is
-2) is negative, as in every reference converter (`is_return_line = qty < 0`) and plan_check (return_lines ↔ a
negative quantity). return_lines = at least one such line. False with the reason when qty is not mapped (biz_110,
dev_009, dev_012: one row per order / ticket).

Only the yes / no is the plan's. What follows from it is the converter's fixed code, as in the references (biz_117,
dev_004): a return line is its own line with its own order id and the return's date; return_date = that date;
order_date / order_time = the earliest of the order's sale lines; every line of an order with a return line is
`returned` (rulebook §1).

The return lines' rows are kept (`rows`): 6a's per-order check leaves them out (their own date is expected).

Warnings (trace only, no fix): a negative qty with a positive line_amount, or a negative line_amount on a line whose
qty is not negative (a discount / voucher row? step 7), and qty / amount cells that do not read as numbers (step 2's
column check lets up to 20 % through).
"""
from dataclasses import dataclass, field

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.parse import is_blank, parse_money, parse_qty

EXAMPLES = 3  # rows shown per kind


@dataclass
class ReturnLines:
    qty_col: str | None
    amount_col: str | None
    return_lines: bool = False  # the plan's return_lines
    rows: set = field(default_factory=set)  # (explore.table_key, row number) of every return line
    count: int = 0  # return lines
    examples: list = field(default_factory=list)  # "<table> row N: qty …, amount …"
    positive_amount: list = field(default_factory=list)  # negative qty, positive amount (count kept in the length)
    negative_amount: list = field(default_factory=list)  # negative amount, qty not negative
    unreadable: dict = field(default_factory=dict)  # column -> cells that did not parse
    skipped: str | None = None


def _number(f, v):
    """f(v)'s number, None for a blank, "?" for a cell that does not read."""
    if is_blank(v):
        return None
    try:
        x = f(v)
    except ValueError:
        return "?"
    return x[0] if isinstance(x, tuple) else x


def return_lines(tables: list[explore.Table], columns: dict[str, str]) -> ReturnLines:
    """6b on the line tables, with step 2's mapping {field: raw column}."""
    out = ReturnLines(columns.get("qty"), columns.get("line_amount"))
    if out.qty_col is None:
        out.skipped = "no qty column mapped: no line can be a negative-quantity return"
        return out
    for t in tables:
        jq = explore.column_index(t, out.qty_col)
        ja = explore.column_index(t, out.amount_col) if out.amount_col else None
        for i in t.data_numbers():
            r = t.rows[i - 1]
            vq = r[jq] if jq is not None and jq < len(r) else None
            va = r[ja] if ja is not None and ja < len(r) else None
            q, a = _number(parse_qty, vq), _number(parse_money, va)
            for col, x, v in ((out.qty_col, q, vq), (out.amount_col, a, va)):
                if x == "?":
                    out.unreadable.setdefault(col, []).append(v)
            q = q if isinstance(q, float) else None
            a = a if isinstance(a, float) else None
            where = f"{label(t.file, t.sheet)} row {i}: qty {explore._cell(vq)}, amount {explore._cell(va)}"
            if q is not None and q < 0:
                out.rows.add((explore.table_key(t.file, t.sheet), i))
                out.count += 1
                if len(out.examples) < EXAMPLES:
                    out.examples.append(where)
                if a is not None and a > 0:
                    out.positive_amount.append(where)
            elif a is not None and a < 0:
                out.negative_amount.append(where)
    out.return_lines = out.count > 0
    return out


def problems(rl: ReturnLines) -> list[str]:
    """The trace warnings."""
    out = []
    if rl.positive_amount:
        out.append(f"{len(rl.positive_amount)} line(s) with a negative qty but a positive amount, e.g. "
                   + "; ".join(rl.positive_amount[:EXAMPLES]))
    if rl.negative_amount:
        out.append(f"{len(rl.negative_amount)} line(s) with a negative amount but a qty that is not negative "
                   "(discount / voucher rows? step 7), e.g. " + "; ".join(rl.negative_amount[:EXAMPLES]))
    for col, cells in rl.unreadable.items():
        out.append(f"{len(cells)} cell(s) of {col!r} do not read as numbers, e.g. "
                   + ", ".join(map(repr, cells[:EXAMPLES])))
    return out
