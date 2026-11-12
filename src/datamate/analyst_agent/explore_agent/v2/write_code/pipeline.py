"""write_code's fixed pipeline (no LLM in any stage; explore_agent.md "write_code"). Built so far: stages 1-4.

1. read every line table (header rows, skipped rows, subtotal regexes, source_*), concat in plan order;
2. forward fill the plan's forward_fill_columns over the concatenated lines (common.forward_fill);
3. joins in plan order, as row pointers (joins.py);
4. select and rename: one column per ColumnSource, raw cells (parsing is stage 5).
"""
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan
from src.datamate.analyst_agent.explore_agent.v2.write_code.joins import Joined, apply_joins, through
from src.datamate.analyst_agent.explore_agent.v2.write_code.tables import (SOURCE_COLUMNS, ReadReport, find_column,
                                                                             is_line_table, read_line_tables, ref_key)
from src.datamate.reference.common import forward_fill


@dataclass
class Applied:
    lines: pd.DataFrame  # stage 4: one column per mapped schema column (raw cells) + source_*
    raw_lines: pd.DataFrame  # the line tables after stages 1-2, every raw column (stage 6's duplicate rule reads it)
    joined: Joined  # stage 3's tables and row pointers
    read: ReadReport
    notes: list[str] = field(default_factory=list)


def select_columns(raw_lines: pd.DataFrame, joined: Joined, plan: SalesLinePlan) -> pd.DataFrame:
    """Stage 4: each ColumnSource's raw values under its schema column name, one row per line."""
    out = pd.DataFrame(index=raw_lines.index)
    for c in plan.columns:
        if is_line_table(plan, c.file, c.sheet):
            out[c.schema_column] = raw_lines[find_column(raw_lines, c.raw_column)]
            continue
        key = ref_key((c.file, c.sheet))
        if key not in joined.tables:
            raise ValueError(f"{c.schema_column}: {c.file} [{c.sheet or ''}] is neither a line table nor joined")
        out[c.schema_column] = through(joined, key, find_column(joined.tables[key], c.raw_column))
    for s in SOURCE_COLUMNS:
        out[s] = raw_lines[s]
    return out


def apply_stages_1_4(shop_dir: Path, plan: SalesLinePlan) -> Applied:
    read = ReadReport()
    notes: list[str] = []
    raw = read_line_tables(shop_dir, plan, read)  # 1
    if plan.forward_fill_columns:  # 2
        cols = [find_column(raw, c) for c in plan.forward_fill_columns]
        raw = forward_fill(raw, cols, notes)
    joined = apply_joins(shop_dir, raw, plan, read)  # 3
    lines = select_columns(raw, joined, plan)  # 4
    return Applied(lines=lines, raw_lines=raw, joined=joined, read=read, notes=notes)
