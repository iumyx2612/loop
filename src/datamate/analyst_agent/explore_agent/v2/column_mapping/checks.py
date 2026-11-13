"""Step 2's checks, plain code: does a mapped column hold what its field needs? Run on every line table.

- Column checks (check_answer, CHECKS): of the column's non-blank cells, at least MIN_PARSED must read as the kind
  of value the field holds (date, money, quantity, ...). Blank cells are not counted, so forward-fill gaps do not
  fail a column. status: at most MAX_STATUS_WORDS distinct values. Text fields have no check.
- The cross-column check, with code's repair of the amount fields and the qty_base decision: amounts.py (user,
  2026-11-07). When code cannot repair the amounts, CROSS_FIELDS are the ones asked again.
- The shared-column check (check_shared_columns): two fields never point to the same table column, except the
  pairs in SHARED_OK (one cell holding both values, as the first prompt says). It catches qty and qty_base both on
  dev_003's 'SL (ĐV cơ bản)': the two columns differ only on the rare big-unit lines, too few for the cross-column
  check. It cannot tell which field is wrong: every field on that column is asked again, and the retry shows the
  rows where the shared column and its look-alike columns differ (evidence_rows).

Every problem is written in two parts (user, 2026-11-07): one line per answer in question (`field ← 'column'`),
then the explanation on the next line. With this format and the evidence rows, the 27B fixed dev_003's qty /
qty_base; with the same evidence but the answers inside the sentence, it swapped them (test.py runs 4–7).
"""
import datetime as dt
import re
from typing import Callable

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.plan_check import MAX_STATUS_WORDS
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.amounts import AMOUNT_FIELDS, MIN_FIT  # noqa: F401
from src.datamate.parse import is_blank, norm_text, parse_date, parse_datetime, parse_money, parse_qty

MIN_PARSED = 0.8  # share of a column's non-blank cells that must read as the field's kind of value
CROSS_FIELDS = ("qty", "qty_base", "unit_price", "line_amount", "line_discount")  # asked again when it fails
# fields that may share one table column: a cell like '3 chai' (qty + unit), a date-time cell (date + time)
SHARED_OK = ({"qty", "unit"}, {"order_date", "order_time"})
EVIDENCE_ROWS = 3  # rows shown in a retry where a shared column and a look-alike column differ
LOOK_ALIKE = 0.8  # a look-alike holds the same value as the shared column on at least this share of rows
LOOK_ALIKE_FILLED = 0.5  # ... of at least this share of the rows having a value in both


def answer_lines(answers: list[tuple[str, str]]) -> str:
    """The first part of a problem: one line per answer in question."""
    return "\n".join(f"{f} ← '{c}'" for f, c in answers)


# --- does a cell read as the kind of value the field holds? (True / False, never raises) ---

def _reads(f: Callable) -> Callable:
    def check(v) -> bool:
        try:
            return bool(f(v))
        except (ValueError, TypeError, OverflowError):
            return False
    return check


def _money(v):
    return parse_money(v) is not None  # not bool(parse_money(v)): a 0 discount is valid money


def _date(v):
    d = parse_date(v, default_year=2000)  # a year-less 'd/m' (biz_117) is a date too; the year does not matter here
    return 2000 <= d.year <= 2100  # an Excel serial is any int: a quantity column would read as 1900 dates


def _time(v):
    if isinstance(v, (dt.datetime, dt.time)):
        return True
    return re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", str(v).strip()) or parse_datetime(v)


CHECKS: dict[str, tuple[str, Callable]] = {  # field -> (what the cells must read as, check)
    "order_date": ("dates", _reads(_date)),
    "order_time": ("times", _reads(_time)),
    "line_amount": ("money", _reads(_money)),
    "unit_price": ("money", _reads(_money)),
    "cost_price": ("money", _reads(_money)),
    "line_discount": ("money", _reads(_money)),
    "qty": ("quantities", _reads(lambda v: parse_qty(v)[0] is not None)),
    "qty_base": ("quantities", _reads(lambda v: parse_qty(v)[0] is not None)),
}


def non_blank_cells(tables: list[explore.Table], column: str) -> list:
    """The non-blank cells of `column` in the data rows (the filter step's rows left out), over all line tables."""
    out = []
    for t in tables:
        j = explore.column_index(t, column)
        if j is not None:
            out += [r[j] for r in t.data_rows() if j < len(r) and not is_blank(r[j])]
    return out


def check_answer(field: str, column: str, tables: list[explore.Table]) -> tuple[str | None, dict]:
    """(problem or None, the check's record for the trace). Fields without a check (text) always pass."""
    if field not in CHECKS and field != "status":
        return None, {}
    cells = non_blank_cells(tables, column)
    head = answer_lines([(field, column)])
    if not cells:
        return f"{head}\nThe column is empty in the line tables", {"non_blank": 0}
    if field == "status":
        distinct = len({str(v).strip() for v in cells})
        rec = {"non_blank": len(cells), "distinct": distinct}
        if distinct > MAX_STATUS_WORDS:
            return (f"{head}\n{distinct} different values, too many for a status column "
                    f"(at most {MAX_STATUS_WORDS})"), rec
        return None, rec
    kind, ok = CHECKS[field]
    bad = [v for v in cells if not ok(v)]
    share = 1 - len(bad) / len(cells)
    rec = {"non_blank": len(cells), "read": round(share, 3)}
    if share >= MIN_PARSED:
        return None, rec
    examples = ", ".join(explore._cell(v) for v in list({str(v): v for v in bad}.values())[:3])  # as stored
    rec["examples"] = examples
    return (f"{head}\nOnly {share:.0%} of its {len(cells):,} non-blank cells read as {kind} "
            f"(e.g. {examples})"), rec


def check_shared_columns(mapped: dict[str, str]) -> tuple[list[str], set[str], dict]:
    """Fields pointing to the same table column, other than a SHARED_OK pair. mapped: field -> table column (only
    mapped fields, no NOT_HERE). (one problem per shared column, the fields to ask again, record for the trace)."""
    by_column: dict[str, list[str]] = {}
    for f, column in mapped.items():
        by_column.setdefault(column, []).append(f)
    problems, retry, rec = [], set(), {}
    for column, fields in by_column.items():
        if len(fields) < 2 or any(set(fields) <= ok for ok in SHARED_OK):
            continue
        rec[column] = fields
        retry.update(fields)
        allowed = " or ".join(" + ".join(sorted(ok)) for ok in SHARED_OK)
        problems.append(f"{answer_lines([(f, column) for f in fields])}\nEach sales_lines column needs its own "
                        f"table column (only {allowed} may share one), so some of these are wrong. Give each the "
                        f"column that really holds it, or \"(not in this file)\"")
    return problems, retry, rec


def _value_key(v):
    """A cell compared across columns: its quantity when it reads as one ('1 ly' = 1), else its normalized text."""
    if is_blank(v):
        return None
    try:
        q = parse_qty(v)[0]
    except (ValueError, TypeError):
        q = None
    return q if q is not None else norm_text(str(v))


def _cell(t: explore.Table, i: int, j: int):
    r = t.rows[i - 1]
    return r[j] if j < len(r) else None


def look_alikes(t: explore.Table, column: str) -> list[str]:
    """The other columns of t holding the same value as `column` on most rows (dev_003: 'S.L' for 'SL (ĐV cơ
    bản)'): at least LOOK_ALIKE of the rows where both have a value, on at least LOOK_ALIKE_FILLED of the rows."""
    j = explore.column_index(t, column)
    if j is None:
        return []
    data = t.data_numbers()
    keys = [_value_key(_cell(t, i, j)) for i in data]
    out = []
    for k, name in enumerate(t.columns):
        if k == j:
            continue
        both = [(a, b) for a, i in zip(keys, data) if a is not None and (b := _value_key(_cell(t, i, k))) is not None]
        if len(both) >= LOOK_ALIKE_FILLED * len(data) and sum(a == b for a, b in both) >= LOOK_ALIKE * len(both):
            out.append(name)
    return out


def evidence_rows(tables: list[explore.Table], column: str,
                  n: int = EVIDENCE_ROWS) -> tuple[list[str], explore.Table | None, list[int]]:
    """The rows where `column` and a look-alike column hold different values: the rows a 5-row table hides and
    that tell the two apart (dev_003's 'hộp' lines: 'SL (ĐV cơ bản)' 6, 'S.L' 1). Look-alikes are found on the
    first line table; the rows are the first n of the first table that has any (row numbers as in the file).
    (look-alike columns, that table, row numbers); no look-alike or no such row → ([...], None, [])."""
    alikes = look_alikes(tables[0], column) if tables else []
    if not alikes:
        return alikes, None, []
    for t in tables:
        j, ks = explore.column_index(t, column), [explore.column_index(t, a) for a in alikes]
        if j is None:
            continue
        rows = []
        for i in t.data_numbers():
            a = _value_key(_cell(t, i, j))
            if a is not None and any(k is not None and (b := _value_key(_cell(t, i, k))) is not None and b != a
                                     for k in ks):
                rows.append(i)
                if len(rows) == n:
                    break
        if rows:
            return alikes, t, rows
    return alikes, None, []

