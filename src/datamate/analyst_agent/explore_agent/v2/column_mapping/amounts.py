"""Step 2's amount fields decided by code (user, 2026-11-07): qty, unit_price, line_amount, line_discount and qty_base.

The amount fields form an equation the data must satisfy: line_amount = qty × unit_price − line_discount (the survey:
97–99 % of lines in every shop). So code can test the model's answer on every row, and when it does not fit, find
the fitting answer closest to it, without asking the model again.

- fit (check_amounts): at least MIN_FIT of the lines fit (tolerance 1 VND or 0.1 %), and no unit (the column mapped
  to `unit`) with at least UNIT_LINES lines of which more than UNIT_FAIL miss. The per-unit part sees what the
  overall share hides: dev_003's qty ← 'SL (ĐV cơ bản)' fits 99.9 % of ~100,000 lines but all 67 'hộp' lines miss.
- repair (repair_amounts): when the answer does not fit, code tries every answer that changes one amount field, then
  two, among the candidate columns (those that read as the field's kind, see _Columns). Rules: a column holds at
  most one amount field; "no discount column" wins a tie (dev_014: line_discount 'Bớt' → none, 100 %; 'SL' would
  also fit within 1 VND, being 1 on every line); then the higher fit. One best answer → it replaces the model's; a
  tie or no fitting answer → no repair: the cross-column problem goes back to the model as before.
- qty_base (decide_qty_base): the other quantity column whose ratio to qty is one value per product and unit (1 on
  base-unit lines, the factor on big-unit lines: dev_003 'hộp' 6/1 for SP0049). The model's column is kept when it
  passes; else the one passing column is taken; else qty_base is left open. Without a unit column the ratio may
  differ per unit sold (biz_119 sp0013: 1, 4, 16), so then: ratio ≥ 1, mostly 1, few values per product.

Tested on 21 saved mappings (14 public test + 10 train shops, 3 without line amounts skipped; scratchpad
solve_amounts.py, 2026-11-07): the model's answer fits on 20; dev_014 and dev_003's earlier qty answer are repaired
to the reference's answer by one change; qty_base passes on every shop where the model mapped it.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations, product

import numpy as np

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.parse import is_blank, norm_text, parse_money, parse_qty

AMOUNT_FIELDS = ("qty", "unit_price", "line_amount", "line_discount")  # the equation's fields
MIN_FIT = 0.9  # share of lines on which line_amount = qty × unit_price − line_discount
UNIT_LINES = 10  # the per-unit check looks at units with at least this many lines ...
UNIT_FAIL = 0.5  # ... and fails one when more than this share of its lines miss
MIN_READ = 0.8  # a candidate column: this share of its non-blank cells reads as the field's kind
MIN_FILLED = 0.5  # qty / unit_price / line_amount candidates: filled and non-zero on this share of the rows
MIN_LINES = 0.5  # an answer is tested on at least this share of the rows (all of qty, price, amount read)
MAX_CHANGES = 2  # the repair changes at most this many amount fields
QB_CONSTANT = 0.95  # qty_base with a unit column: share of (product, unit) groups with a single ratio
QB_MOSTLY_ONE = 0.5  # qty_base: share of lines where it equals qty (sold in the base unit)
QB_MAX_RATIOS = 5  # qty_base without a unit column: at most this many ratios per product
NONE = None  # "no column" for line_discount


def _number(f, v) -> float:
    """The cell as a number; nan when blank or unreadable."""
    if is_blank(v):
        return np.nan
    try:
        x = f(v)
    except (ValueError, TypeError, OverflowError):
        return np.nan
    return np.nan if x is None else float(x)


def _qty(v):
    return parse_qty(v)[0]


class _Columns:
    """The line tables' cells as numbers, one column at a time, read once (all line tables, data rows only: the
    header and the filter step's rows left out). `where[i]` = (table label, row number in the file) of row i, for
    the examples."""

    def __init__(self, tables: list[explore.Table]):
        self.tables = tables
        self.where = [(label(t.file, t.sheet), i) for t in tables for i in t.data_numbers()]
        self.n = len(self.where)
        self._cells, self._num, self._text = {}, {}, {}

    def cells(self, column: str) -> list:
        if column not in self._cells:
            out = []
            for t in self.tables:
                j = explore.column_index(t, column)
                out += [(r[j] if j is not None and j < len(r) else None) for r in t.data_rows()]
            self._cells[column] = out
        return self._cells[column]

    def money(self, column: str) -> np.ndarray:
        return self._numbers(column, "money", parse_money)

    def qty(self, column: str) -> np.ndarray:
        return self._numbers(column, "qty", _qty)

    def _numbers(self, column: str, kind: str, f) -> np.ndarray:
        if (column, kind) not in self._num:
            self._num[column, kind] = np.array([_number(f, v) for v in self.cells(column)], dtype=float)
        return self._num[column, kind]

    def text(self, column: str | None) -> np.ndarray:
        if column is None:
            return np.full(self.n, None, dtype=object)
        if column not in self._text:
            self._text[column] = np.array([None if is_blank(v) else norm_text(str(v)) for v in self.cells(column)],
                                          dtype=object)
        return self._text[column]

    def reads(self, column: str, kind: str, need_filled: bool) -> bool:
        """A candidate for a field of this kind: MIN_READ of its non-blank cells read; for qty / price / amount
        also filled and non-zero on MIN_FILLED of the rows (a mostly-0 column 'fits' as 0 × anything = 0)."""
        a = self.money(column) if kind == "money" else self.qty(column)
        filled = sum(not is_blank(v) for v in self.cells(column))
        if not filled or (~np.isnan(a)).sum() < MIN_READ * filled:
            return False
        return not need_filled or ((~np.isnan(a)) & (a != 0)).sum() >= MIN_FILLED * self.n


@dataclass
class Fit:
    fit: float = 0.0
    lines: int = 0
    bad_units: list = field(default_factory=list)  # [(unit, lines, share missing)]
    misses: list = field(default_factory=list)  # a few (where, qty, price, discount, amount, expected)

    @property
    def ok(self) -> bool:
        return self.fit >= MIN_FIT and not self.bad_units

    def record(self) -> dict:
        rec = {"lines": self.lines, "fit": round(self.fit, 3)}
        if self.bad_units:
            rec["bad_units"] = [{"unit": u, "lines": n, "missing": round(s, 3)} for u, n, s in self.bad_units]
        return rec


def _fit(cols: _Columns, a: dict, units: np.ndarray | None, examples: int = 0) -> Fit:
    q, p, amount = cols.qty(a["qty"]), cols.money(a["unit_price"]), cols.money(a["line_amount"])
    d = cols.money(a["line_discount"]) if a.get("line_discount") else None
    ok = ~np.isnan(q) & ~np.isnan(p) & ~np.isnan(amount)
    n = int(ok.sum())
    if not n:
        return Fit()
    dd = np.nan_to_num(d) if d is not None else 0.0
    expected = q * p - dd
    hit = np.abs(amount - expected) <= np.maximum(1.0, np.abs(amount) * 0.001)
    out = Fit(fit=float(hit[ok].mean()), lines=n)
    if units is not None:
        groups = defaultdict(lambda: [0, 0])
        for u, h in zip(units[ok], hit[ok]):
            g = groups[u or "(blank)"]
            g[0] += 1
            g[1] += not h
        out.bad_units = sorted(((u, k, m / k) for u, (k, m) in groups.items() if k >= UNIT_LINES and m / k > UNIT_FAIL),
                               key=lambda x: -x[1])
    if examples:
        miss = np.flatnonzero(ok & ~hit)
        if out.bad_units:  # show the lines of the failing unit first: they are what tells the columns apart
            bad = {u for u, _, _ in out.bad_units}
            miss = sorted(miss, key=lambda i: (units[i] or "(blank)") not in bad)
        dv = d if d is not None else np.zeros(cols.n)
        out.misses = [(cols.where[i], q[i], p[i], np.nan_to_num(dv[i]), amount[i], expected[i]) for i in miss[:examples]]
    return out


@dataclass
class AmountResult:
    fit: Fit
    repaired: dict = field(default_factory=dict)  # field -> new column (None = no column) when code repaired
    record: dict = field(default_factory=dict)


def check_amounts(mapped: dict[str, str], tables: list[explore.Table], unit_column: str | None,
                  repair: bool = True, cols: _Columns | None = None) -> AmountResult | None:
    """The equation on the mapped amount fields (None when qty, unit_price or line_amount is not mapped, e.g. a
    table with one row per order). When it does not fit and `repair`, the closest fitting answer (repair_amounts)."""
    if not all(f in mapped for f in ("qty", "unit_price", "line_amount")):
        return None
    cols = cols or _Columns(tables)
    units = cols.text(unit_column) if unit_column else None
    answer = {f: mapped.get(f) for f in AMOUNT_FIELDS}
    fit = _fit(cols, answer, units, examples=3)
    result = AmountResult(fit=fit, record=fit.record())
    if fit.ok or not repair:
        return result
    new, rec = repair_amounts(cols, answer, units)
    result.record["repair"] = rec
    if new:
        result.repaired = {f: new[f] for f in AMOUNT_FIELDS if new[f] != answer[f]}
    return result


def repair_amounts(cols: _Columns, answer: dict, units: np.ndarray | None) -> tuple[dict | None, dict]:
    """The fitting answer closest to `answer` (fewest amount fields changed, at most MAX_CHANGES), or None when
    there is none or several equally good ones. (answer or None, record for the trace)."""
    columns = cols.tables[0].columns
    candidates = {
        "qty": [c for c in columns if cols.reads(c, "qty", True)],
        "unit_price": [c for c in columns if cols.reads(c, "money", True)],
        "line_amount": [c for c in columns if cols.reads(c, "money", True)],
        "line_discount": [NONE] + [c for c in columns if cols.reads(c, "money", False)],
    }
    rec = {"candidates": {f: len(v) for f, v in candidates.items()}}
    for k in range(1, MAX_CHANGES + 1):
        found = []
        for changed in combinations(AMOUNT_FIELDS, k):
            for values in product(*(candidates[f] for f in changed)):
                a = {**answer, **dict(zip(changed, values))}
                if any(a[f] == answer[f] for f in changed):
                    continue  # not a change
                used = [a[f] for f in AMOUNT_FIELDS if a[f] is not None]
                if len(used) != len(set(used)):
                    continue  # a column holds at most one amount field
                fit = _fit(cols, a, units)
                if fit.ok and fit.lines >= MIN_LINES * cols.n:
                    found.append((a, fit))
        if not found:
            continue
        # "no discount column" wins a tie, then the higher fit
        found.sort(key=lambda x: (x[0]["line_discount"] is not None, -x[1].fit))
        best, fit = found[0]
        rec.update(changes=k, fitting=len(found),
                   best={f: best[f] for f in AMOUNT_FIELDS if best[f] != answer[f]}, best_fit=round(fit.fit, 3))
        if len(found) > 1:
            b2, f2 = found[1]
            if (b2["line_discount"] is None) == (best["line_discount"] is None) and abs(f2.fit - fit.fit) < 1e-3:
                rec["tie"] = {f: b2[f] for f in AMOUNT_FIELDS if b2[f] != answer[f]}
                return None, rec  # two equally good answers: the model decides
        return best, rec
    rec["none_fits"] = True
    return None, rec


def problem_text(mapped: dict[str, str], result: AmountResult) -> str:
    """The cross-column problem (two parts: the answers, then why), with the example lines."""
    used = [(f, mapped[f]) for f in AMOUNT_FIELDS if f in mapped]
    head = "\n".join(f"{f} ← '{c}'" for f, c in used)
    examples = "; ".join(f"{where} row {i}: {q:g} × {p:,.0f} − {d:,.0f} = {e:,.0f}, but line_amount is {a:,.0f}"
                         for (where, i), q, p, d, a, e in result.fit.misses)
    discount_note = "" if "line_discount" in mapped else " (no line_discount column mapped: counted as 0)"
    why = (f"line_amount = qty × unit_price − line_discount holds on only {result.fit.fit:.0%} of "
           f"{result.fit.lines:,} lines{discount_note}")
    if result.fit.bad_units:
        u, n, s = result.fit.bad_units[0]
        why += f", and on the {n:,} lines with unit '{u}' it fails on {s:.0%}"
    return f"{head}\n{why}; one of these columns is probably wrong. E.g. {examples}"


def decide_qty_base(tables: list[explore.Table], qty_column: str, current: str | None, product_column: str | None,
                    unit_column: str | None, cols: _Columns | None = None) -> tuple[str | None, dict]:
    """qty_base: the model's column if it passes the ratio test, else the one other quantity column that does,
    else None (open). (column or None, record for the trace)."""
    cols = cols or _Columns(tables)
    keys = cols.text(product_column)
    units = cols.text(unit_column) if unit_column else None
    rec = {"current": current}
    if current and current != qty_column:
        test = _ratio_test(cols, qty_column, current, keys, units)
        rec["current_test"] = test
        if test["passed"]:
            rec["decided"] = current
            return current, rec
    passing = []
    for c in cols.tables[0].columns:
        if c in (qty_column, current) or not cols.reads(c, "qty", True):
            continue
        test = _ratio_test(cols, qty_column, c, keys, units)
        if test["passed"]:
            passing.append((c, test))
    rec["passing"] = [c for c, _ in passing]
    rec["decided"] = passing[0][0] if len(passing) == 1 else None
    return rec["decided"], rec


def _ratio_test(cols: _Columns, qty_column: str, column: str, keys: np.ndarray, units: np.ndarray | None) -> dict:
    """Does `column` / qty behave like a conversion factor? With a unit column: one ratio per (product, unit) on
    QB_CONSTANT of the groups. Without: at most QB_MAX_RATIOS ratios per product. Both: ratio ≥ 1 on every line
    tested, equal to 1 on QB_MOSTLY_ONE of them, tested on MIN_LINES of the rows."""
    q, y = cols.qty(qty_column), cols.qty(column)
    ok = ~np.isnan(q) & ~np.isnan(y) & (q != 0)
    n = int(ok.sum())
    if n < MIN_LINES * cols.n:
        return {"passed": False, "lines": n}
    ratio = np.round(y[ok] / q[ok], 6)
    groups = defaultdict(set)
    k = keys[ok]
    u = units[ok] if units is not None else [None] * n
    for key, unit, r in zip(k, u, ratio):
        groups[(key, unit) if units is not None else key].add(r)
    one = float((ratio == 1).mean())
    at_least_one = float((ratio >= 1 - 1e-9).mean())
    if units is not None:
        shape = sum(len(s) == 1 for s in groups.values()) / len(groups)
        shape_ok = shape >= QB_CONSTANT
    else:
        shape = max(len(s) for s in groups.values())
        shape_ok = shape <= QB_MAX_RATIOS
    passed = shape_ok and one >= QB_MOSTLY_ONE and at_least_one >= 0.999
    return {"passed": passed, "lines": n, "equal_to_qty": round(one, 4), "groups": len(groups),
            ("constant_groups" if units is not None else "max_ratios_per_product"): round(shape, 3)}
