"""Stage 5 of write_code (fixed code, no LLM): every mapped column into one format per kind, the same for every shop.

The kind is fixed by the schema column (stage 4 already renamed the columns), so `KINDS` is one table for all shops
and each kind has one parser (parse.py, as the reference converters use them). Agreed with the user (2026-11-12,
explore_agent.md "write_code"):
- a cell the parser rejects is a failure: counted per column (distinct values, counts, first row, error) and set
  to null; the column is still converted (d). The failure report is the handoff to the LLM part (not built);
- `status` is stage 6 (a lookup in the plan's statuses), passed through raw here (a);
- channel / payment_method: the fixed word lists; an unknown word is a failure (b);
- order_time: parse_datetime on the cell, as every reference (every known time column holds a full date-time) (c).
Text kinds as the references: ids / codes / names / seller -> clean_text; groups and units -> norm_text.
Year-less dates ('2/6'; rulebook §8: the year of the sheet the row is in; agreed with the user 2026-11-12): per
sheet, (a) a 4-digit year in the sheet name and (b) the year of the sheet's full dates; the year is used only where
both agree, else the cells stay failures with the reason. When the name also gives a month (T06-2025), a year-less
date whose month differs is a failure too (catches a month-first shop). See `sheet_contexts`.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan
from src.datamate.parse import (clean_text, is_blank, map_channel, map_payment, norm_text, parse_date,
                                parse_datetime, parse_money, parse_qty, strip_phone)

RAW = "raw"  # passed through unchanged (stage 6 reads it)

KINDS = {
    "order_id": "code", "product_code": "code", "customer_code": "code",
    "seller": "text", "product_name": "text", "line_product_name": "text",
    "customer_name": "customer_name",
    "customer_group": "label", "product_group": "label", "unit": "label", "base_unit": "label",
    "order_date": "date", "order_time": "datetime",
    "unit_price": "money", "line_discount": "money", "line_amount": "money", "cost_price": "money",
    "qty": "qty", "qty_base": "qty",
    "channel": "channel", "payment_method": "payment",
    "status": RAW,
}


def code_text(v) -> str | None:
    """An id or code as text; a whole number stored as a float (Excel) loses its '.0' (123.0 -> '123')."""
    if isinstance(v, float) and not is_blank(v) and v.is_integer():
        v = int(v)
    return clean_text(v)


def qty_value(v) -> float | None:
    return parse_qty(v)[0]


PARSERS: dict[str, Callable] = {
    "code": code_text,
    "text": clean_text,
    "customer_name": strip_phone,
    "label": norm_text,
    "date": parse_date,
    "datetime": parse_datetime,
    "money": parse_money,
    "qty": qty_value,
    "channel": map_channel,
    "payment": map_payment,
}


@dataclass
class ColumnReport:
    column: str
    kind: str
    filled: int = 0  # non-blank raw cells
    failed: int = 0  # cells the parser rejected (now null)
    # the failing distinct values, most frequent first: (value repr, count, 'file [sheet] row n', error)
    failures: list[tuple[str, int, str, str]] = field(default_factory=list)
    note: str = ""
    sheets: list[str] = field(default_factory=list)  # date columns: how each sheet's year-less dates got a year


@dataclass
class Normalized:
    lines: pd.DataFrame  # stage 5 output: parsed values (python objects; schema.finalize casts them later)
    columns: dict[str, ColumnReport]


def parse_column(s: pd.Series, parse: Callable, where: pd.Series, report: ColumnReport,
                 max_examples: int = 5, extra: pd.Series | None = None) -> pd.Series:
    """`parse` on every cell (each distinct value parsed once); a rejected cell -> None, counted in `report`.
    `extra`: a second argument per row (hashable), parse(cell, extra), e.g. the sheet's year for a date."""
    cache: dict = {}
    errors: dict = {}  # failing value key -> (repr, error, first row)
    counts: Counter = Counter()
    out = []
    extras = extra if extra is not None else [None] * len(s)
    call = (lambda v, e: parse(v, e)) if extra is not None else (lambda v, e: parse(v))
    for v, w, e in zip(s, where, extras):
        if is_blank(v):
            out.append(None)
            continue
        report.filled += 1
        try:
            key = (type(v), v, e)
            hash(key)
        except TypeError:
            key = (type(v), repr(v), e)
        if key in cache:
            out.append(cache[key])
            continue
        if key not in errors:
            try:
                cache[key] = call(v, e)
                out.append(cache[key])
                continue
            except (ValueError, TypeError, OverflowError) as e:
                errors[key] = (repr(v), f"{type(e).__name__}: {e}", w)
        out.append(None)
        report.failed += 1
        counts[key] += 1
    report.failures = [(errors[k][0], n, errors[k][2], errors[k][1]) for k, n in counts.most_common(max_examples)]
    return pd.Series(out, index=s.index, dtype=object)


# ---------- year-less dates ----------

YEARLESS = re.compile(r"\s*\d{1,2}/\d{1,2}\s*")  # the form parse_date reads only with a default_year
NAME_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
MIN_DATA_YEAR = 0.9  # (b): the sheet's full dates must be this much one year


def is_yearless(v) -> bool:
    return isinstance(v, str) and YEARLESS.fullmatch(v) is not None


def name_year_month(name: str) -> tuple[int | None, int | None]:
    """(a): the one 4-digit year in a sheet / file name, and the one 1-2 digit number 1-12 left beside it as its month:
    'T06-2025' -> (2025, 6), 'Tháng 01-2025' -> (2025, 1), 'BanHang_2025' -> (2025, None)."""
    years = set(NAME_YEAR.findall(name))
    if len(years) != 1:
        return None, None
    year = years.pop()
    rest = re.sub(rf"(?<!\d){year}(?!\d)", " ", name)
    months = {int(m) for m in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", rest) if 1 <= int(m) <= 12}
    return int(year), (months.pop() if len(months) == 1 else None)


def sheet_contexts(raw: pd.Series, files: pd.Series, sheets: pd.Series) -> tuple[pd.Series, list[str]]:
    """Per row: (year, month, reason) of its sheet, for sheets with year-less dates (None elsewhere); and one
    report line per such sheet. year None = not decided (reason says why); month None = the name gives none."""
    ctx = pd.Series([None] * len(raw), index=raw.index, dtype=object)
    lines = []
    for (file, sheet), idx in raw.groupby([files, sheets], sort=False).groups.items():
        cells = raw.loc[idx]
        yearless = cells.map(is_yearless)
        if not yearless.any():
            continue
        # (a) the sheet name (a csv or a nameless sheet: the file name)
        name_year, name_month = name_year_month(str(sheet)) if sheet else (None, None)
        named_by = "sheet name"
        if name_year is None:
            name_year, name_month = name_year_month(str(file).rsplit(".", 1)[0])
            named_by = "file name"
        # (b) the year of the sheet's full dates
        years: Counter = Counter()
        for v, n in Counter(v for v in cells[~yearless] if not is_blank(v)).items():
            try:
                years[parse_date(v).year] += n
            except ValueError:
                pass
        data_year, share = (years.most_common(1)[0][0], years.most_common(1)[0][1] / sum(years.values())) \
            if years else (None, 0.0)
        if share < MIN_DATA_YEAR:
            data_year = None
        if name_year is None:
            year, reason = None, "no year in the sheet or file name"
        elif data_year is None:
            year, reason = None, ("no full dates in the sheet" if not years
                                  else f"the sheet's full dates have no one year ({dict(years)})")
        elif name_year != data_year:
            year, reason = None, f"{named_by} says {name_year}, the sheet's full dates {data_year}"
        else:
            year, reason = name_year, ""
        ctx.loc[idx] = pd.Series([(year, name_month, reason)] * len(idx), index=idx, dtype=object)
        where = f"{file} [{sheet}]" if sheet else str(file)
        lines.append(f"{where}: {int(yearless.sum()):,} year-less -> "
                     + (f"year {year} ({named_by} + full dates agree)" if year else f"NO YEAR: {reason}")
                     + (f", month {name_month} checked" if year and name_month else ""))
    return ctx, lines


def parse_date_in_sheet(v, ctx):
    """parse_date; a year-less cell takes its sheet's year (ctx = (year, month, reason) from sheet_contexts)."""
    if ctx is None or not is_yearless(v):
        return parse_date(v)
    year, month, reason = ctx
    if year is None:
        raise ValueError(f"year-less date, sheet year not decided: {reason}")
    d = parse_date(v, year)
    if month is not None and d.month != month:
        raise ValueError(f"year-less date in month {d.month}, but the sheet name says month {month}")
    return d


def same_source(plan: SalesLinePlan, a: str, b: str) -> bool:
    """Two schema columns read from one raw column (qty + unit on '3 chai')."""
    src = {c.schema_column: (c.file, c.sheet, c.raw_column) for c in plan.columns}
    return a in src and src.get(a) == src.get(b)


def normalize(lines: pd.DataFrame, plan: SalesLinePlan) -> Normalized:
    """Stage 5 on stage 4's output (one raw column per schema column + source_*)."""
    where = (lines["source_file"].astype(str) + " [" + lines["source_sheet"].astype(str) + "] row "
             + lines["source_row"].astype(str))
    out = pd.DataFrame(index=lines.index)
    reports: dict[str, ColumnReport] = {}
    for col in lines.columns:
        if col.startswith("source_"):
            out[col] = lines[col]
            continue
        kind = KINDS.get(col)
        if kind is None or kind == RAW:
            out[col] = lines[col]
            reports[col] = ColumnReport(col, kind or "unknown", note="passed through raw"
                                        + ("" if kind else ": no kind for this column"))
            continue
        report = reports[col] = ColumnReport(col, kind)
        if kind == "date":
            ctx, report.sheets = sheet_contexts(lines[col], lines["source_file"], lines["source_sheet"])
            out[col] = parse_column(lines[col], parse_date_in_sheet, where, report, extra=ctx)
        else:
            out[col] = parse_column(lines[col], PARSERS[kind], where, report)

    # qty + unit (common.parse_qty_and_unit): the unit column wins; a line without one takes the unit written in
    # the qty cell ('3 chai'); when unit and qty are one raw column, the unit is the one in the cell
    if "qty" in lines.columns:
        in_cell = lines["qty"].map(lambda v: _unit_in_cell(v))
        n_in_cell = int(in_cell.notna().sum())
        if "unit" not in lines.columns or same_source(plan, "unit", "qty"):
            out["unit"] = in_cell
            if "unit" in reports:
                reports["unit"].note = "unit and qty are one raw column: the unit written in the qty cell"
        else:
            out["unit"] = out["unit"].where(out["unit"].notna(), in_cell)
        if n_in_cell:
            reports["qty"].note = f"{n_in_cell:,} qty cells carry a unit (used where the line has no unit)"
    return Normalized(out, reports)


def _unit_in_cell(v) -> str | None:
    try:
        return parse_qty(v)[1]
    except ValueError:
        return None  # already a qty failure
