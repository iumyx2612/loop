"""Stage 1 of write_code (fixed code, no LLM): read the plan's tables into DataFrames.

Tables are read through explore.tables with the plan's header_rows and skipped_rows, so the pipeline sees exactly
the rows, row numbers and column names the explore steps saw. Cells stay raw Python values (parsing is stage 5).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan, TableRef
from src.datamate.analyst_agent.explore_agent.v2.planner.filter_rows.rows import row_text
from src.datamate.parse import fold_accents, is_blank

SOURCE_COLUMNS = ["source_file", "source_sheet", "source_row"]


@dataclass
class ReadReport:
    """What reading dropped, for the run report."""
    tables: list[str] = field(default_factory=list)  # 'file [sheet]: n rows'
    skipped: int = 0  # rows in plan.skipped_rows
    blank: int = 0  # blank rows inside the data
    subtotal: int = 0  # rows matching a subtotal_labels regex that skipped_rows did not cover
    renamed: list[str] = field(default_factory=list)  # repeated column names renamed '<name>#<k>'


def unique_columns(columns: list[str], where: str, report: ReadReport) -> list[str]:
    """A repeated header name keeps the first; later ones become '<name>#<k>' (a DataFrame needs unique names)."""
    seen: dict[str, int] = {}
    out = []
    for c in columns:
        if c in seen:
            seen[c] += 1
            new = f"{c}#{seen[c]}"
            report.renamed.append(f"{where}: {c!r} -> {new!r}")
            out.append(new)
        else:
            seen[c] = 0
            out.append(c)
    return out


def read_table(shop_dir: Path, file: str, sheet: str | None, plan: SalesLinePlan, report: ReadReport,
               subtotals: list[re.Pattern] = ()) -> pd.DataFrame:
    """Every data row of (file, sheet) (sheet None = every sheet of an xlsx), with source_file / source_sheet /
    source_row (row numbers as in the file). `subtotals`: regexes matched against row_text (line tables only)."""
    frames = []
    for t in explore.tables(shop_dir, file, sheet, plan.header_rows, plan.skipped_rows):
        where = f"{t.file} [{t.sheet}]" if t.sheet else t.file
        columns = unique_columns(t.columns, where, report)
        report.skipped += len(t.skipped)
        records, numbers = [], []
        for n in t.data_numbers():
            r = t.rows[n - 1]
            if all(is_blank(v) for v in r):
                report.blank += 1
                continue
            if subtotals and (text := row_text(r)) and any(p.search(text) for p in subtotals):
                report.subtotal += 1
                continue
            records.append(r)
            numbers.append(n)
        df = pd.DataFrame.from_records(records, columns=columns).astype(object) if records \
            else pd.DataFrame(columns=columns, dtype=object)
        df["source_file"] = t.file
        df["source_sheet"] = t.sheet or ""
        df["source_row"] = numbers
        frames.append(df)
        report.tables.append(f"{where}: {len(df):,} rows")
    if not frames:
        raise ValueError(f"{file} [{sheet}]: no table")
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


def read_line_tables(shop_dir: Path, plan: SalesLinePlan, report: ReadReport) -> pd.DataFrame:
    """Stage 1: the same reading on every line table (month sheet, split file), concatenated in plan order."""
    subtotals = [re.compile(p, re.IGNORECASE) for p in plan.subtotal_labels]
    frames = [read_table(shop_dir, r.file, r.sheet, plan, report, subtotals) for r in plan.line_tables]
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


def find_column(df: pd.DataFrame, column: str) -> str:
    """The frame's name for a plan column: exact, else accent- and case-insensitive (as explore.column_index)."""
    if column in df.columns:
        return column
    key = fold_accents(column).lower()
    hits = [c for c in df.columns if fold_accents(str(c)).lower() == key]
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"column {column!r} not in the table (columns: {', '.join(map(str, df.columns))})")


def is_line_table(plan: SalesLinePlan, file: str, sheet: str | None) -> bool:
    """A plan reference to any line table (write_plan names the line group by its first table) means the lines."""
    return any(r.file == file and (r.sheet == sheet or r.sheet is None or sheet is None) for r in plan.line_tables)


def ref_key(ref: TableRef | tuple[str, str | None]) -> str:
    file, sheet = (ref.file, ref.sheet) if isinstance(ref, TableRef) else ref
    return explore.table_key(file, sheet)
