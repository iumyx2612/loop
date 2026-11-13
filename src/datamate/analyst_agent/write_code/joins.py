"""Stage 3 of write_code (fixed code, no LLM): apply the plan's joins.

Each joined table stays its own DataFrame. A join gives every line a row pointer into the joined table (its row
position, or None when nothing matches); a chained join (biz_108: customers -> orders -> lines) looks up its on_key
through the pointer of its on_file. Stage 4 reads a joined column through the pointer, so no names collide.

Values are compared with attach's value_key (no accents, case or extra spaces), the comparison that proved the link.
A key value on several rows: the first row wins (attach allows 1 % repeats), counted in the report.
"""
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate.analyst_agent.explore_agent.state import Join, SalesLinePlan
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.analyst_agent.write_code.tables import (ReadReport, find_column, is_line_table,
                                                                             read_table, ref_key)


@dataclass
class Joined:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)  # table_key -> the joined table
    pointers: dict[str, pd.Series] = field(default_factory=dict)  # table_key -> row position per line (or None)
    report: list[dict] = field(default_factory=list)  # one record per join


def key_index(table: pd.DataFrame, columns: list[str]) -> tuple[dict[str, int], int]:
    """value_key -> first row position, over the key column(s); and how many rows lost to an earlier row."""
    index: dict[str, int] = {}
    repeated = 0
    for col in columns:
        own: set[str] = set()
        for pos, v in enumerate(table[col]):
            k = value_key(v)
            if k is None:
                continue
            if k in own:  # the same value twice in one key column: the first row wins
                repeated += 1
                continue
            own.add(k)
            index.setdefault(k, pos)  # a name equal to another row's code (alt_key): the code column wins
    return index, repeated


def through(joined: Joined, key: str, column: str) -> pd.Series:
    """The values of a joined table's column, one per line (None where the line has no matching row)."""
    values = joined.tables[key][column].to_numpy(dtype=object)
    return joined.pointers[key].map(lambda p: None if p is None or pd.isna(p) else values[int(p)])


def apply_join(shop_dir: Path, lines: pd.DataFrame, plan: SalesLinePlan, join: Join, joined: Joined,
               read_report: ReadReport) -> None:
    key = ref_key((join.file, join.sheet))
    table = read_table(shop_dir, join.file, join.sheet, plan, read_report)
    key_cols = [find_column(table, join.key)] + ([find_column(table, join.alt_key)] if join.alt_key else [])
    index, repeated = key_index(table, key_cols)

    if is_line_table(plan, join.on_file, join.on_sheet):
        on_values = lines[find_column(lines, join.on_key)]
    else:
        on_key = ref_key((join.on_file, join.on_sheet))
        if on_key not in joined.tables:
            raise ValueError(f"join {join.file} -> {join.on_file}: {join.on_file} is not joined before it")
        on_values = through(joined, on_key, find_column(joined.tables[on_key], join.on_key))

    pointer = on_values.map(lambda v: index.get(value_key(v)))
    joined.tables[key] = table
    joined.pointers[key] = pointer
    filled = on_values.map(lambda v: value_key(v) is not None)
    found = pointer.notna() & filled
    joined.report.append({
        "join": f"{join.file} [{join.sheet or ''}] {' / '.join(key_cols)} -> {join.on_file} {join.on_key}",
        "rows": len(table),
        "lines_with_key": int(filled.sum()),
        "lines_matched": int(found.sum()),
        "repeated_keys": repeated,
        "unmatched_examples": list(dict.fromkeys(str(v) for v in on_values[filled & ~found]))[:5],
    })


def apply_joins(shop_dir: Path, lines: pd.DataFrame, plan: SalesLinePlan, read_report: ReadReport) -> Joined:
    """Stage 3: the plan's joins in order (a join's on_file is the lines or a table joined before it)."""
    joined = Joined()
    for join in plan.joins:
        apply_join(shop_dir, lines, plan, join, joined, read_report)
    return joined
