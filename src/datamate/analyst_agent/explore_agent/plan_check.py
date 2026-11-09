"""plan_check: checks a SalesLinePlan against the shop's files in plain code, before any converter is written.

Every problem is one sentence that says what is wrong and what to do, so the explore_agent can fix its plan
(the 9B rule: checks in code that say exactly what is wrong). An empty list means the plan passed.
Tables are read through explore.tables, i.e. with the same header rows the agent's tools used.
"""
from collections import Counter
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan
from src.datamate.parse import clean_text, fold_accents, is_blank
from src.datamate.reference.common import SUBTOTAL

# sales_lines columns the plan must either map or list as missing. line_no is not asked (user, 2026-11-07): no query
# tool or rulebook rule uses it, and a running-number column (STT) is easily taken for it instead of the order id
# (dev_014); the schema keeps the column, null in agent conversions
MAPPABLE = ("order_id", "order_date", "order_time", "status", "customer_name", "customer_group",
            "channel", "payment_method", "seller", "product_code", "product_name", "product_group", "unit", "qty",
            "base_unit", "unit_price", "line_amount", "cost_price")
# mapped when the shop records them, otherwise computed by the converter (not needed in missing_columns).
# customer_code and line_product_name are working columns the converter reads but does not store: customer_key
# = the customer code when the shop has codes (§3); line_product_name = the name written on the line, used when
# product_name comes from the product list and a line's code is not in it (found in biz_108, 128, dev_003,
# 006, 010 and biz_108, dev_003 by a scan of the reference converters, 2026-11-02)
OPTIONAL = ("qty_base", "line_discount", "customer_code", "line_product_name")
# never mapped: the converter computes them. return_date (moved from OPTIONAL, user, 2026-11-07): the date column a
# return line carries is the one mapped to order_date; the converter takes return_date from it on return lines and
# order_date from the order's sale lines (biz_117, dev_004). No query tool or rulebook rule reads it
DERIVED = ("customer_key", "product_key", "line_kind", "is_return_line", "return_date", "unit_factor",
           "source_file", "source_sheet", "source_row")
ALWAYS_RECORDED = ("order_date", "line_amount")  # every shop has them; a plan without them is wrong
MAX_STATUS_WORDS = 40  # more distinct values than this: probably not a status column
MIN_JOIN_COVERAGE = 0.5  # share of the on_key values that must be found in the joined table
MAX_BLANK_ORDER_IDS = 0.01  # blank order ids above this share (outside subtotal rows) need forward fill


def _label(file: str, sheet: str | None) -> str:
    return file if sheet is None else f"{file} [{sheet}]"


def _key_text(v) -> str | None:
    """A cell as a join key: codes stored as numbers (123.0) and as text ('123') compare equal."""
    if is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return clean_text(str(v))


class _Shop:
    """The shop's tables, read once per check."""

    def __init__(self, shop_dir: Path, headers: dict | None):
        self.shop_dir, self.headers = shop_dir, headers
        self.problems: list[str] = []

    def tables(self, file: str, sheet: str | None, what: str) -> list[explore.Table]:
        try:
            return explore.tables(self.shop_dir, file, sheet, self.headers)
        except explore.ExploreError as e:
            self.problems.append(f"{what}: {e}")
            return []

    def cells(self, file: str, sheet: str | None, column: str, what: str) -> list | None:
        """Every cell of `column` below the header, over all tables of (file, sheet); None (+ a problem) when
        the table or the column does not exist."""
        ts = self.tables(file, sheet, what)
        if not ts:
            return None
        found = [(t, explore.column_index(t, column)) for t in ts]
        found = [(t, i) for t, i in found if i is not None]
        if not found:
            self.problems.append(f"{what}: {_label(file, sheet)} has no column {column!r}. "
                                 f"Its columns: {', '.join(ts[0].columns)}")
            return None
        return [r[i] if i < len(r) else None for t, i in found for r in t.rows[t.header_row:]]


def _line_cells(shop: _Shop, plan: SalesLinePlan, column: str, what: str) -> list | None:
    """A column over every line table (split files, month sheets); None when no line table has it."""
    out, seen = [], False
    for ref in plan.line_tables:
        for t in shop.tables(ref.file, ref.sheet, what):
            i = explore.column_index(t, column)
            if i is not None:
                seen = True
                out += [r[i] if i < len(r) else None for r in t.rows[t.header_row:]]
    if not seen:
        shop.problems.append(f"{what}: no line table has a column {column!r}")
        return None
    return out


def _is_line(plan: SalesLinePlan, file: str, sheet: str | None) -> bool:
    """(file, sheet) is a line table. By sheet, not just by file: biz_116 keeps the product list ('Danh sách hàng
    hóa') in the same workbook as the lines ('So_chi_tiet_ban_hang'). A line table without a sheet = every sheet."""
    return any(ref.file == file and (ref.sheet is None or ref.sheet == sheet) for ref in plan.line_tables)


def _source_cells(shop: _Shop, plan: SalesLinePlan, file: str, sheet: str | None, column: str, line_files: set[str],
                  what: str) -> list | None:
    """The cells a mapped column is read from: over every line table when (file, sheet) is one of them (split
    files and month sheets with one header are read together, so a value written only in the 2026 file still
    counts), else the named table only."""
    if _is_line(plan, file, sheet):
        return _line_cells(shop, plan, column, what)
    return shop.cells(file, sheet, column, what)


def _line_rows(shop: _Shop, plan: SalesLinePlan) -> list[tuple[explore.Table, tuple]]:
    return [(t, r) for ref in plan.line_tables for t in shop.tables(ref.file, ref.sheet, "line_tables")
            for r in t.rows[t.header_row:]]


def _is_subtotal_row(r: tuple, labels: list[str]) -> bool:
    """A subtotal / footer row (the fixed SUBTOTAL pattern or one of the plan's subtotal_labels), or a blank row."""
    texts = [v for v in r if isinstance(v, str) and v.strip()]
    if not any(not is_blank(v) for v in r):
        return True
    folded = [fold_accents(v) for v in texts]
    return any(SUBTOTAL.match(v) for v in texts) or any(fold_accents(lb) in f for lb in labels for f in folded)


def _check_columns(shop: _Shop, plan: SalesLinePlan, line_files: set[str], joined: set[str]) -> dict[str, str]:
    """The column mapping; returns schema column -> raw column for the columns that exist."""
    p = shop.problems
    raw_of: dict[str, str] = {}
    counts = Counter(c.schema_column for c in plan.columns)
    for name, n in counts.items():
        # One entry per line file with the same raw column (split files: _1, _2026) is one mapping, not a duplicate
        sources = {(line, fold_accents(c.raw_column) if line else (c.file, c.sheet, c.raw_column))
                   for c in plan.columns if c.schema_column == name for line in [_is_line(plan, c.file, c.sheet)]}
        if n > 1 and (len(sources) > 1 or not next(iter(sources))[0]):
            p.append(f"columns: {name} is mapped {n} times from different columns; map it once (the column the "
                     "rulebook reads). Split line files with the same header need only one entry")
    for c in plan.columns:
        what = f"columns[{c.schema_column}]"
        if c.schema_column in DERIVED:
            p.append(f"{what}: {c.schema_column} is computed by the converter; remove it from columns")
            continue
        if c.schema_column not in MAPPABLE + OPTIONAL:
            p.append(f"{what}: {c.schema_column!r} is not a sales_lines column. "
                     f"Columns: {', '.join(MAPPABLE + OPTIONAL)}")
            continue
        if c.file not in line_files | joined:
            p.append(f"{what}: {c.file} is neither a line table nor joined to one; add it to line_tables or joins")
        if c.schema_column == "cost_price" and not _is_line(plan, c.file, c.sheet):
            # Rulebook §5: gross profit uses the cost written on the sale line; a product list's cost is today's
            # cost, not the cost at the sale (biz_116 'Giá vốn', live run 6)
            p.append(f"{what}: cost_price must be the cost written on the sale line, in a line table; "
                     f"{_label(c.file, c.sheet)} is not one (a cost in a product or import list is not the cost at the sale). If no line table "
                     "records it, list cost_price in missing_columns")
        if shop.cells(c.file, c.sheet, c.raw_column, what) is not None:
            raw_of[c.schema_column] = c.raw_column
    for name in plan.missing_columns:
        # An OPTIONAL column listed as missing is true and harmless (the field says "columns the shop does not
        # record"): not a problem. Sonnet did it in 2 of 3 biz_108 runs and each rejection cost a think +
        # write_plan round (2026-11-02).
        if name in OPTIONAL:
            continue
        if name not in MAPPABLE:
            p.append(f"missing_columns: {name!r} is not a column you must map "
                     f"({'computed by the converter' if name in DERIVED else 'unknown'}); remove it")
        elif name in counts:
            p.append(f"missing_columns: {name} is also in columns; keep one")
    unaccounted = [c for c in MAPPABLE if c not in counts and c not in plan.missing_columns]
    if unaccounted:
        p.append(f"columns: not mapped and not in missing_columns: {', '.join(unaccounted)}. "
                 "Find where the shop records each, in any file, or list it as missing")
    for name in ALWAYS_RECORDED:
        if name in plan.missing_columns:
            p.append(f"missing_columns: every shop records {name}; find its column")
    return raw_of


def _check_joins(shop: _Shop, plan: SalesLinePlan, line_files: set[str]) -> None:
    p = shop.problems
    attached = set(line_files)
    for j in plan.joins:
        what = f"joins[{j.file} -> {j.on_file}]"
        if j.on_file not in attached:
            p.append(f"{what}: on_file {j.on_file} is not a line table or a table joined before this one")
        keys = shop.cells(j.file, j.sheet, j.key, what)
        on = _source_cells(shop, plan, j.on_file, j.on_sheet, j.on_key, line_files, what)
        attached.add(j.file)
        if keys is None or on is None:
            continue
        keys = [k for k in map(_key_text, keys) if k is not None]
        dup = [k for k, n in Counter(keys).items() if n > 1]
        if dup:
            p.append(f"{what}: key {j.key!r} is not unique in {j.file} ({len(dup)} values repeat, e.g. "
                     f"{', '.join(map(repr, dup[:3]))}); a join key must name one row")
        wanted = {k for k in map(_key_text, on) if k is not None}
        if wanted:
            found = len(wanted & set(keys)) / len(wanted)
            if found < MIN_JOIN_COVERAGE:
                example = sorted(wanted - set(keys))[:3]
                p.append(f"{what}: only {found:.0%} of the {j.on_key!r} values are found in {j.file} {j.key!r} "
                         f"(not found e.g. {', '.join(map(repr, example))}); the columns do not link these tables")


def _check_statuses(shop: _Shop, plan: SalesLinePlan, line_files: set[str]) -> None:
    p = shop.problems
    src = next((c for c in plan.columns if c.schema_column == "status"), None)
    if src is None:
        if plan.statuses:
            p.append("statuses: given, but status is not mapped to a column; map it or empty statuses")
        return
    cells = _source_cells(shop, plan, src.file, src.sheet, src.raw_column, line_files, "statuses")
    if cells is None:
        return
    in_file = Counter(clean_text(str(v)) for v in cells if not is_blank(v))
    if len(in_file) > MAX_STATUS_WORDS:
        p.append(f"statuses: {src.raw_column!r} has {len(in_file)} distinct values; is it really the order status?")
        return
    given = {fold_accents(s.raw) for s in plan.statuses}
    missing = [w for w in in_file if fold_accents(w) not in given]
    if missing:
        p.append(f"statuses: these values of {src.raw_column!r} have no status: "
                 + ", ".join(f"{w!r} ×{in_file[w]}" for w in missing[:15]))
    folded_file = {fold_accents(w) for w in in_file}
    unknown = [s.raw for s in plan.statuses if fold_accents(s.raw) not in folded_file]
    if unknown:
        p.append(f"statuses: not values of {src.raw_column!r}: {', '.join(map(repr, unknown))}; "
                 "use the values exactly as written")
    # 'hủy' and 'Hủy' are two real values and may both be listed; only one word with two statuses is wrong
    by_word: dict[str, set[str]] = {}
    for s in plan.statuses:
        by_word.setdefault(fold_accents(s.raw), set()).add(s.status)
    conflicts = [w for w, st in by_word.items() if len(st) > 1]
    if conflicts:
        p.append("statuses: the same word is given different statuses: "
                 + ", ".join(f"{w!r} ({' / '.join(sorted(by_word[w]))})" for w in conflicts) + "; give each one status")


def _check_forward_fill(shop: _Shop, plan: SalesLinePlan, raw_of: dict[str, str]) -> None:
    p = shop.problems
    order_col = raw_of.get("order_id")
    ff = plan.forward_fill_columns
    if ff:
        if order_col is None:
            p.append("forward_fill_columns: given, but order_id is not mapped; forward fill needs the order id")
        elif fold_accents(ff[0]) != fold_accents(order_col):
            p.append(f"forward_fill_columns: must start with the order-id column {order_col!r}")
        for col in ff:
            cells = _line_cells(shop, plan, col, f"forward_fill_columns[{col}]")
            if cells is not None and not any(is_blank(v) for v in cells):
                p.append(f"forward_fill_columns: {col!r} is never blank, so there is nothing to fill; remove it")
        return
    if order_col is None:
        return
    rows = [(t, r) for t, r in _line_rows(shop, plan) if not _is_subtotal_row(r, plan.subtotal_labels)]
    blank = 0
    for t, r in rows:
        i = explore.column_index(t, order_col)
        if i is not None and is_blank(r[i] if i < len(r) else None):
            blank += 1
    if rows and blank / len(rows) > MAX_BLANK_ORDER_IDS:
        p.append(f"forward_fill_columns: {blank} of {len(rows)} line rows have a blank {order_col!r} (outside "
                 "subtotal rows). If they continue the order above, list the order-level columns to fill; if they "
                 "are subtotal or footer rows, put their text in subtotal_labels")


def _check_line_texts(shop: _Shop, plan: SalesLinePlan, raw_of: dict[str, str]) -> None:
    p = shop.problems
    rows = _line_rows(shop, plan)
    folded = [[fold_accents(v) for v in r if isinstance(v, str)] for _, r in rows]
    for field in ("service_fee_names", "non_revenue_names", "subtotal_labels"):
        for text in getattr(plan, field):
            key = fold_accents(text)
            if not any(key in cell for cells in folded for cell in cells):
                p.append(f"{field}: {text!r} is not in any line table; use the text as it is written")
    if "qty" in raw_of:
        # both ways: return_lines true needs negative quantities, and negative quantities need return_lines true
        # (a look at the first rows misses return lines further down)
        cells = _line_cells(shop, plan, raw_of["qty"], "return_lines") or []
        negative = [v for v in cells if (isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0)
                    or (isinstance(v, str) and v.strip().startswith("-"))]
        if plan.return_lines and not negative:
            p.append(f"return_lines: true, but {raw_of['qty']!r} has no negative quantity")
        elif negative and not plan.return_lines:
            p.append(f"return_lines: false, but {len(negative)} lines have a negative {raw_of['qty']!r} (e.g. "
                     f"{', '.join(map(repr, negative[:3]))}). If they record goods coming back, set return_lines "
                     "true (ask the search agent to show a few of them first if unsure)")


def _check_conversions(shop: _Shop, plan: SalesLinePlan, raw_of: dict[str, str]) -> None:
    p = shop.problems
    if plan.conversions == "file":
        if plan.conversion_table is None:
            p.append("conversion_table: conversions = file, so name the conversion file")
        else:
            shop.tables(plan.conversion_table.file, plan.conversion_table.sheet, "conversion_table")
    elif plan.conversion_table is not None:
        p.append(f"conversion_table: given, but conversions = {plan.conversions}; set conversions = file or drop it")
    if plan.conversions == "column" and "qty_base" not in raw_of:
        p.append("conversions: = column, so map qty_base to the line's base-unit quantity column")


def check_plan(shop_dir: Path, plan: SalesLinePlan, headers: dict | None = None) -> list[str]:
    shop = _Shop(Path(shop_dir), headers)
    if not plan.line_tables:
        return ["line_tables: empty; name the table(s) whose rows are the sales lines"]
    for ref in plan.line_tables:
        shop.tables(ref.file, ref.sheet, "line_tables")
    line_files = {ref.file for ref in plan.line_tables}
    joined = {j.file for j in plan.joins}
    raw_of = _check_columns(shop, plan, line_files, joined)
    _check_joins(shop, plan, line_files)
    _check_statuses(shop, plan, line_files)
    _check_forward_fill(shop, plan, raw_of)
    _check_line_texts(shop, plan, raw_of)
    _check_conversions(shop, plan, raw_of)
    for a in plan.extra_attributes:
        what = f"extra_attributes[{a.name}]"
        if a.file not in line_files | joined:
            shop.problems.append(f"{what}: {a.file} is neither a line table nor joined to one")
        shop.cells(a.file, a.sheet, a.raw_column, what)
    return list(dict.fromkeys(shop.problems))  # same problem from two checks: once
