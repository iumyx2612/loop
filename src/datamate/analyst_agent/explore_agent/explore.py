"""The explore_agent's read-only tools, as plain functions (the LangGraph wrappers live in tools.py).

Every function takes the shop folder and returns a short text for the LLM. Shared conventions:
- A "table" is one xlsx sheet, one csv file or one sqlite table, addressed by (file, sheet).
  For a csv the sheet is ignored; for a sqlite file the sheet is the table name.
- Rows are numbered as in the file: row 1 is the first row of the sheet (for csv / sqlite: the column names),
  so `peek(start=...)` and `find_rows` use the same numbers, and they match `source_row` of common.read_xlsx.
- The header row is guessed (see `_guess_header`); columns are named by their header cell, or `_col<i>`
  (0-based) when that cell is empty, like common.read_xlsx.
- xlsx with sheet=None means every sheet of the file (month-per-sheet books share one header). column_values and
  find_rows without a sheet also read the split files with the same header (_1, _2026); peek stays on one file.
- Outputs are capped (cells shortened, at most MAX_CHARS characters) so a 100k-row file never floods the context.
"""
import csv
import datetime as dt
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import openpyxl

from src.datamate.analyst_agent.explore_agent.readme import readme_columns
from src.datamate.parse import fold_accents, is_blank

DATA_SUFFIXES = (".xlsx", ".csv", ".sqlite")
MAX_CHARS = 6000  # cap on one tool output
CELL_CHARS = 32  # cap on one cell in peek / find_rows
MAX_PEEK_ROWS = 30
MAX_DISTINCT = 30  # column_values lists every value up to this many distinct values
MAX_FOUND_ROWS = 15
HEADER_SCAN_ROWS = 10


class ExploreError(ValueError):
    """A bad argument (unknown file, sheet, column); the message tells the agent what exists."""


@dataclass
class Table:
    file: str
    sheet: str | None  # None for csv
    rows: list[tuple]  # every row of the sheet, trailing blank rows and columns removed
    header_row: int  # 1-based
    columns: list[str]
    header_source: str  # "readme": the row holding the README's column names; "guess": _guess_header
    readme_missing: list[str]  # README columns not found in the header row
    # Rows the filter step (v2 filter_rows) marked as not data: repeated headers, subtotal / footer / note rows.
    # `rows` keeps them (rows stay numbered as in the file); readers that want data use data_numbers / data_rows
    skipped: frozenset[int] = frozenset()

    def data_numbers(self) -> list[int]:
        """Row numbers (as in the file) below the header, without the rows the filter step marked."""
        return [i for i in range(self.header_row + 1, len(self.rows) + 1) if i not in self.skipped]

    def data_rows(self) -> list[tuple]:
        """The rows below the header, without the rows the filter step marked."""
        if not self.skipped:
            return self.rows[self.header_row:]
        return [self.rows[i - 1] for i in self.data_numbers()]


# ---------- reading (cached: the agent calls several tools on the same sheet) ----------

def _trim(rows: list[tuple]) -> list[tuple]:
    """Drop blank rows at the end and blank columns at the right (openpyxl reports formatted empty cells)."""
    last = 0
    width = 0
    for i, r in enumerate(rows):
        filled = [j for j, v in enumerate(r) if not is_blank(v)]
        if filled:
            last = i + 1
            width = max(width, filled[-1] + 1)
    return [tuple(r[:width]) + (None,) * (width - len(r)) for r in rows[:last]]


@lru_cache(maxsize=16)
def _read(path: str, mtime: float) -> dict[str | None, list[tuple]]:
    """All tables of one file: {sheet: rows}. `mtime` is part of the cache key only."""
    p = Path(path)
    if p.suffix == ".xlsx":
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        out = {sn: _trim(list(wb[sn].iter_rows(values_only=True))) for sn in wb.sheetnames}
        wb.close()
        return out
    if p.suffix == ".csv":
        with open(p, encoding="utf-8-sig", newline="") as fh:
            return {None: _trim([tuple(r) for r in csv.reader(fh)])}
    if p.suffix == ".sqlite":
        out = {}
        with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as con:
            names = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            for t in names:
                cur = con.execute(f'SELECT * FROM "{t}"')
                out[t] = [tuple(c[0] for c in cur.description)] + [tuple(r) for r in cur.fetchall()]
        return out
    raise ExploreError(f"{p.name}: not a data file (expected {', '.join(DATA_SUFFIXES)})")


def _natural(name: str) -> list:
    """Sort key that puts T2 before T10."""
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", name)]


def _data_files(shop_dir: Path) -> list[Path]:
    return sorted((p for p in shop_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in DATA_SUFFIXES and not p.name.startswith("~$")),
                  key=lambda p: _natural(p.name))


def _file(shop_dir: Path, file: str) -> Path:
    p = shop_dir / file
    if p.parent != shop_dir or not p.is_file() or p.suffix.lower() not in DATA_SUFFIXES:
        names = ", ".join(f.name for f in _data_files(shop_dir))
        raise ExploreError(f"no data file {file!r} in this shop. Files: {names}")
    return p


def _sheets(shop_dir: Path, file: str) -> dict[str | None, list[tuple]]:
    p = _file(shop_dir, file)
    return _read(str(p), p.stat().st_mtime)


def _guess_header(rows: list[tuple], is_xlsx: bool) -> int:
    """1-based header row. csv / sqlite: row 1. xlsx: among the first rows, the first one with the most filled
    cells that are all text (title rows have one or two cells; data rows usually hold numbers or dates)."""
    if not is_xlsx or not rows:
        return 1
    scan = rows[:HEADER_SCAN_ROWS]
    counts = [sum(not is_blank(v) for v in r) for r in scan]
    texty = [i for i, r in enumerate(scan) if counts[i] and all(isinstance(v, str) for v in r if not is_blank(v))]
    pool = texty or range(len(scan))
    best = max(counts[i] for i in pool)
    return next(i for i in pool if counts[i] == best) + 1


def _column_names(header: tuple) -> list[str]:
    return [str(h).strip() if not is_blank(h) else f"_col{i}" for i, h in enumerate(header)]


def _readme_header(rows: list[tuple], names: list[str]) -> tuple[int, list[str]] | None:
    """The row (1-based) among the first rows that holds most of the README's column names, and the names
    it lacks. None when no row holds at least half of them (then the README does not describe this sheet)."""
    wanted = {fold_accents(n): n for n in names}
    best, best_row = 0, None
    for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
        cells = {fold_accents(str(v)) for v in r if not is_blank(v)}
        score = sum(k in cells for k in wanted)
        if score > best:
            best, best_row = score, i
    if best_row is None or best * 2 < len(wanted):
        return None
    cells = {fold_accents(str(v)) for v in rows[best_row] if not is_blank(v)}
    return best_row + 1, [n for k, n in wanted.items() if k not in cells]


def table_key(file: str, sheet: str | None) -> str:
    """Key of a table in `headers` (and in the graph state): 'file::sheet', 'file::' for a csv."""
    return f"{file}::{sheet or ''}"


def _table(shop_dir: Path, file: str, sheet: str | None, rows: list[tuple], headers: dict | None,
           skipped: dict | None = None) -> Table:
    """Header row, in order of trust: confirmed (`headers`, e.g. by the LLM fallback), the row holding the
    README's column names, the guess. `skipped`: the filter step's rows per table_key (state["skipped_rows"]);
    None = nothing skipped (before the filter step, and peek / find_rows, which show the file as it is)."""
    spec = readme_columns(shop_dir, file, sheet)
    found = _readme_header(rows, spec.columns) if spec else None
    confirmed = (headers or {}).get(table_key(file, sheet))
    if confirmed:
        h, missing, source = confirmed, [], "confirmed"
    elif found:
        h, missing, source = found[0], found[1], "readme"
    else:
        h, missing, source = _guess_header(rows, file.lower().endswith(".xlsx")), [], "guess"
    cols = _column_names(rows[h - 1]) if rows else []
    skip = frozenset((skipped or {}).get(table_key(file, sheet), ()))
    return Table(file, sheet, rows, h, cols, source, missing, skip)


def _tables(shop_dir: Path, file: str, sheet: str | None, headers: dict | None = None,
            skipped: dict | None = None) -> list[Table]:
    """The table(s) a call is about: one sheet, or every sheet of an xlsx when sheet is None."""
    sheets = _sheets(shop_dir, file)
    if not file.lower().endswith(".xlsx") and not file.lower().endswith(".sqlite"):
        return [_table(shop_dir, file, None, sheets[None], headers, skipped)]
    if sheet is None:
        if file.lower().endswith(".sqlite"):
            raise ExploreError(f"{file} is a sqlite file: give the table as sheet. Tables: {', '.join(sheets)}")
        return [_table(shop_dir, file, sn, rows, headers, skipped) for sn, rows in sheets.items()]
    if sheet not in sheets:
        raise ExploreError(f"{file} has no sheet {sheet!r}. Sheets: {', '.join(map(str, sheets))}")
    return [_table(shop_dir, file, sheet, sheets[sheet], headers, skipped)]


def _all_tables(shop_dir: Path, file: str, headers: dict | None = None, skipped: dict | None = None) -> list[Table]:
    """Every table of a file (all sheets, the csv itself, or all sqlite tables)."""
    return [_table(shop_dir, file, sn, rows, headers, skipped) for sn, rows in _sheets(shop_dir, file).items()]


@dataclass
class TableGroup:
    """Tables sharing one header (month sheets, split files): one entry in list_files, one LLM header check."""
    tables: list[Table]

    @property
    def first(self) -> Table:
        return self.tables[0]

    @property
    def needs_header_check(self) -> bool:
        """Only xlsx can have title rows above the header; csv / sqlite headers are row 1 by format.
        A guess of row 1 needs no check either when row 1 is all text: _guess_header then picked it as the
        fullest all-text row, and there is nothing above it to mistake for the header."""
        t = self.first
        if t.header_source != "guess" or not t.file.lower().endswith(".xlsx"):
            return False
        return not _row1_header(t)


def tables(shop_dir: Path, file: str, sheet: str | None, headers: dict | None = None,
           skipped: dict | None = None) -> list[Table]:
    """The table(s) of (file, sheet) exactly as the tools see them; raises ExploreError naming what exists.
    Used by plan_check, which checks the plan against the same tables the agent looked at."""
    return _tables(shop_dir, file, sheet, headers, skipped)


def column_index(t: Table, column: str) -> int | None:
    """Position of `column` in t's header (exact, else accent- and case-insensitive), None when absent."""
    return _find_column(t, column)


def _row1_header(t: Table) -> bool:
    """Guessed header at row 1 and row 1 all text: certain enough without an LLM check."""
    return t.header_row == 1 and bool(t.rows) and all(isinstance(v, str) for v in t.rows[0] if not is_blank(v))


def _group_key(t: Table) -> tuple:
    """Tables with the same key share one header: month sheets, split files (_1, _2026)."""
    return t.header_row, tuple(t.columns), t.header_source, tuple(t.readme_missing)


def table_groups(shop_dir: Path, headers: dict | None = None, skipped: dict | None = None) -> list[TableGroup]:
    groups: dict[tuple, TableGroup] = {}
    for p in _data_files(shop_dir):
        for t in _all_tables(shop_dir, p.name, headers, skipped):
            groups.setdefault(_group_key(t), TableGroup([])).tables.append(t)
    return list(groups.values())


def _with_split_files(shop_dir: Path, file: str, sheet: str | None, headers: dict | None,
                      skipped: dict | None = None) -> list[Table]:
    """The tables of (file, sheet), plus, when no sheet is named (or the file is a csv), the tables of the
    other files with the same header (split files: _1, _2026). So one column_values / find_rows request covers
    the whole group, as list_files shows it and as plan_check reads it. A named sheet stays that one sheet."""
    tables = _tables(shop_dir, file, sheet, headers, skipped)
    is_csv = not file.lower().endswith((".xlsx", ".sqlite"))
    if sheet is not None and not is_csv:
        return tables
    keys = {_group_key(t) for t in tables}
    others = [t for g in table_groups(shop_dir, headers, skipped) for t in g.tables
              if t.file != file and _group_key(t) in keys]
    return tables + others


def _where(tables: list[Table]) -> str:
    """What a call covered: 'X.xlsx [S]', 'X.xlsx (17 sheets)', or 'X_1.xlsx + X_2026.xlsx (19 sheets)'."""
    files = list(dict.fromkeys(t.file for t in tables))
    if len(tables) == 1:
        return _label(tables[0])
    if len(files) == 1:
        return f"{files[0]} ({len(tables)} sheets)"
    n = sum(t.sheet is not None for t in tables)
    return " + ".join(files) + (f" ({n} sheets)" if n else "")


# ---------- formatting ----------

def _cell(v) -> str:
    """A cell as the agent sees it: text in quotes (so '13,353,000' text differs from the number 13353000)."""
    if is_blank(v):
        return ""
    if isinstance(v, dt.datetime):
        s = v.strftime("%Y-%m-%d") if v.time() == dt.time(0) else v.strftime("%Y-%m-%d %H:%M:%S")
        return f"<datetime {s}>"
    if isinstance(v, (dt.date, dt.time)):
        return f"<{type(v).__name__} {v.isoformat()}>"
    if isinstance(v, str):
        s = v if len(v) <= CELL_CHARS else v[:CELL_CHARS - 1] + "…"
        return repr(s)
    if isinstance(v, float) and v.is_integer():
        return f"{v:.1f}"
    return str(v)


def _row_line(n: int, r: tuple) -> str:
    return f"{n:>6} | " + " | ".join(_cell(v) for v in r)


def _columns_line(t: Table) -> str:
    """The guessed header, aligned like a data row, for outputs that do not include the header row."""
    return f"{'cols':>6} | " + " | ".join(t.columns)


def _cap(lines: list[str]) -> str:
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) > MAX_CHARS:
            out.append(f"... output cut ({len(lines) - i} more lines); ask for fewer rows or a narrower search")
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _label(t: Table) -> str:
    return t.file if t.sheet is None else f"{t.file} [{t.sheet}]"


def _shape(v) -> str:
    """Coarse format of a cell: digits -> 9, so '13,353,000' -> 's:9,9,9', '25k' -> 's:9k'."""
    if is_blank(v):
        return "blank"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        kind = "int" if isinstance(v, int) or v.is_integer() else "float"
        return kind + ("<0" if v < 0 else "")
    if not isinstance(v, str):
        return type(v).__name__
    s = re.sub(r"\d+", "9", v.strip())
    return "s:" + (s if len(s) <= 20 else s[:19] + "…")


# ---------- the tools ----------

def read_readme(shop_dir: Path) -> str:
    p = shop_dir / "README.md"
    if not p.exists():
        return "This shop has no README.md."
    return p.read_text(encoding="utf-8")


def _how(t: Table) -> str:
    if t.header_source == "readme":
        return "matches the README's columns"
    if t.header_source == "confirmed":
        return "checked by looking at the first rows (the README lists no columns for it)"
    if not t.file.lower().endswith(".xlsx"):
        return "row 1, as always in a csv / sqlite table"
    if _row1_header(t):
        return "row 1 holds the column names (the README lists no columns for it)"
    return "GUESSED (the README lists no columns for it: check with peek)"


def list_files(shop_dir: Path, headers: dict | None = None) -> str:
    """Every data file and table with its row count, header row and columns.
    Tables with the same header are listed together (month sheets, split files)."""
    lines = []
    for g in table_groups(shop_dir, headers):
        t = g.first
        lines.append(f"== {len(g.tables)} table(s), header at row {t.header_row}, {_how(t)}; "
                     f"{len(t.columns)} columns: {', '.join(t.columns) or '(empty)'}")
        if t.readme_missing:
            lines.append(f"   README columns not in this header: {', '.join(t.readme_missing)}")
        for m in g.tables:
            lines.append(f"   {_label(m)}: rows 1..{len(m.rows)}")
    data_files = _data_files(shop_dir)
    others = sorted(p.name for p in shop_dir.iterdir() if p.is_file() and p not in data_files
                    and p.name != "README.md")
    if others:
        lines.append(f"Other files (not data, not read): {', '.join(others)}")
    return _cap(lines)


def peek(shop_dir: Path, file: str, sheet: str | None = None, start: int = 1, n: int = 10,
         headers: dict | None = None) -> str:
    """n raw rows from row `start` of one table, cells as stored. xlsx with sheet=None: the first sheet."""
    t = _tables(shop_dir, file, sheet, headers)[0]
    n = max(1, min(n, MAX_PEEK_ROWS))
    total = len(t.rows)
    if start < 1 or start > total:
        raise ExploreError(f"{_label(t)} has rows 1..{total}; start={start} is outside")
    end = min(start + n - 1, total)
    lines = [f"{_label(t)}: rows {start}..{end} of {total} (header at row {t.header_row}). "
             "Text cells are in quotes; <datetime …> is a real date cell"]
    if start > t.header_row:
        lines.append(_columns_line(t))
    lines += [_row_line(i, t.rows[i - 1]) for i in range(start, end + 1)]
    return _cap(lines)


def _find_column(t: Table, column: str) -> int | None:
    if column in t.columns:
        return t.columns.index(column)
    key = fold_accents(column)
    matches = [i for i, c in enumerate(t.columns) if fold_accents(c) == key]
    return matches[0] if matches else None


def column_values(shop_dir: Path, file: str, sheet: str | None, column: str, headers: dict | None = None,
                  skipped: dict | None = None) -> str:
    """One column, every row below the header: the distinct values with counts when there are few,
    otherwise the mix of formats with an example of each, plus min / max of numbers and dates.
    Without a sheet: every sheet of the file and every split file with the same header.
    `skipped` (the filter step's rows): those rows are left out."""
    tables = _with_split_files(shop_dir, file, sheet, headers, skipped)
    found = [(t, _find_column(t, column)) for t in tables]
    found = [(t, i) for t, i in found if i is not None]
    if not found:
        raise ExploreError(f"no column {column!r} in {file}{'' if sheet is None else f' [{sheet}]'}. "
                           f"Columns: {', '.join(tables[0].columns)}")
    values: Counter = Counter()
    shapes: Counter = Counter()
    examples: dict[str, object] = {}
    numbers, dates = [], []
    blank = rows = 0
    for t, i in found:
        for r in t.data_rows():
            if all(is_blank(v) for v in r):
                continue
            rows += 1
            v = r[i] if i < len(r) else None
            if is_blank(v):
                blank += 1
                continue
            if len(values) <= MAX_DISTINCT or v in values:
                values[v] += 1
            s = _shape(v)
            shapes[s] += 1
            examples.setdefault(s, v)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numbers.append(v)
            elif isinstance(v, (dt.date, dt.datetime)):
                dates.append(v)
    where = _where([t for t, _ in found])
    lines = [f"{where}, column {found[0][0].columns[found[0][1]]!r}: {rows} rows, {blank} blank"]
    if len(values) <= MAX_DISTINCT:
        lines.append(f"{len(values)} distinct values:")
        lines += [f"   {_cell(v) or '(blank)'} × {c}" for v, c in values.most_common()]
    else:
        lines.append("more than 30 distinct values. Formats (digits shown as 9), with an example:")
        lines += [f"   {s} × {c}   e.g. {_cell(examples[s])}" for s, c in shapes.most_common(12)]
        if len(shapes) > 12:
            lines.append(f"   ... {len(shapes) - 12} more formats")
    if numbers:
        lines.append(f"numbers: min {min(numbers)}, max {max(numbers)}")
    if dates:
        lines.append(f"dates: min {_cell(min(dates))}, max {_cell(max(dates))}")
    return _cap(lines)


def find_rows(shop_dir: Path, file: str, sheet: str | None, text: str, headers: dict | None = None) -> str:
    """The rows holding `text` in any cell (case and accents ignored), with their row numbers.
    Without a sheet: every sheet of the file and every split file with the same header."""
    key = fold_accents(text)
    if not key:
        raise ExploreError("text is empty")
    hits: list[tuple[Table, int]] = []
    per_column: Counter = Counter()
    tables = _with_split_files(shop_dir, file, sheet, headers)
    many_files = len({t.file for t in tables}) > 1

    def where(t: Table) -> str:  # prefix of a row: which table it is in, when the call covered several
        if len(tables) == 1:
            return ""
        return f"{_label(t)} " if many_files else f"[{t.sheet}] "

    for t in tables:
        for n, r in enumerate(t.rows, start=1):
            cols = [j for j, v in enumerate(r) if not is_blank(v) and key in fold_accents(str(v))]
            if cols:
                hits.append((t, n))
                for j in cols:
                    per_column[t.columns[j] if j < len(t.columns) else f"_col{j}"] += 1
    if not hits:
        return f"no row of {_where(tables)} holds {text!r}"
    lines = [f"{_where(tables)}: {len(hits)} rows hold {text!r}; by column: "
             + ", ".join(f"{c} × {k}" for c, k in per_column.most_common(8))]
    lines.append(_columns_line(hits[0][0]))
    shown = hits[:MAX_FOUND_ROWS]
    for t, n in shown:
        lines.append(where(t) + _row_line(n, t.rows[n - 1]))
    if len(hits) > len(shown):
        last_t, last_n = hits[-1]
        lines.append(f"... {len(hits) - len(shown)} more rows; last match at "
                     f"{where(last_t) or ('' if last_t.sheet is None else f'[{last_t.sheet}] ')}row {last_n}")
    return _cap(lines)
