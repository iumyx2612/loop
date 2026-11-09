"""Step 0, filter_rows: mark the rows that are not data (explore_agent.md, "Filter step"), on every table group.

Per table group (rows.py does the code part):
1. Repeated header rows: code, always marked.
2. Candidates (a blank in a dense column) grouped into patterns: code.
3. One structured call per group that has candidates: the header, 3 normal rows, and per pattern ("Group N") its
   row count and up to 3 example rows, each with the row above and below. The reply: one enum per group,
   "data row" / "summary or note row". Every row of a pattern gets the pattern's verdict.
4. Each "summary" pattern with text gets a regex (rows.pattern_regex) for the plan's subtotal_labels, checked here:
   it must match no row that stays data (a problem in the trace otherwise; the marks do not change).

Mark, don't delete: the output is the row numbers per table (`skipped_rows`), rows stay numbered as in the file.
No retry and no special handling of a failed call yet (user, 2026-11-09): with no reply, no pattern is marked;
the repeated headers still are.
"""
import re
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import label, since
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.nodes import readme_meanings_text
from src.datamate.analyst_agent.explore_agent.v2.filter_rows import rows as R
from src.datamate.analyst_agent.explore_agent.v2.filter_rows.prompts import DATA_ROW, FILTER_ROWS, SUMMARY_ROW
from src.datamate.analyst_agent.explore_agent.v2.find_line_tables.nodes import line_table_candidates, option_names
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured

NORMAL_ROWS = 3


# ---------- the prompt ----------

def grid_text(t: explore.Table, rows: list[int], marked: int | None = None) -> str:
    """Header + the given rows of t, aligned like a printed dataframe; first column = row number, the marked row
    prefixed with '>'."""
    grid = [["row", *t.columns]] + [[f"> {i}" if i == marked else str(i), *(explore._cell(v) for v in t.rows[i - 1])]
                                    for i in rows]
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    sizes = [max(len(r[j]) for r in grid) for j in range(width)]
    return "\n".join(" | ".join(c.ljust(s) for c, s in zip(r, sizes)).rstrip() for r in grid)


def normal_text(g: explore.TableGroup, found: R.GroupRows) -> str:
    """The first NORMAL_ROWS data rows that are not candidates, from the first table that has them."""
    cands = {rid for p in found.patterns for rid in p.rows}
    normal = [rid for rid in found.data_rows if rid not in cands][:NORMAL_ROWS]
    if not normal:
        return "(none: every row is in a group below)"
    k = normal[0][0]
    return grid_text(g.tables[k], [i for kk, i in normal if kk == k])


def _example_text(g: explore.TableGroup, k: int, i: int) -> str:
    t = g.tables[k]
    around = [n for n in (i - 1, i, i + 1) if t.header_row < n <= len(t.rows)]
    where = f"(in {label(t.file, t.sheet)})\n" if len(g.tables) > 1 else ""
    return where + grid_text(t, around, marked=i)


def groups_text(g: explore.TableGroup, patterns: list[R.Pattern]) -> str:
    parts = []
    for n, p in enumerate(patterns, 1):
        rows = "1 row" if len(p.rows) == 1 else f"{len(p.rows):,} rows"
        shown = "\n\n".join(_example_text(g, k, i) for k, i in R.examples(p))
        parts.append(f"Group {n}: {rows} like these.\n{shown}")
    return "\n\n".join(parts)


def filter_message(shop_dir: Path, g: explore.TableGroup, name: str, found: R.GroupRows) -> str:
    return FILTER_ROWS.format(readme_meanings=readme_meanings_text(shop_dir, g.first), table=name,
                              rows=len(found.data_rows), normal=normal_text(g, found),
                              groups=groups_text(g, found.patterns), data_row=DATA_ROW, summary_row=SUMMARY_ROW)


def verdict_schema(n: int) -> type[BaseModel]:
    """One field per group, group_1 ... group_n, each an enum of the two verdicts."""
    choice = Literal[DATA_ROW, SUMMARY_ROW]
    return create_model("RowGroups", **{f"group_{k}": (choice, Field(description=f"Group {k}"))
                                        for k in range(1, n + 1)})


# ---------- one table group ----------

def _check_regexes(g: explore.TableGroup, found: R.GroupRows, marked: set[R.RowId], regexes: list[str]) -> list[str]:
    """Each regex must match only marked rows: a match on a row that stays data means the converter would drop it."""
    problems = []
    for rx in regexes:
        pat = re.compile(rx, re.IGNORECASE)
        hits = [(k, i) for k, i in found.data_rows
                if (k, i) not in marked and pat.match(R.row_text(g.tables[k].rows[i - 1]))]
        if hits:
            k, i = hits[0]
            problems.append(f"regex {rx!r} also matches {len(hits)} row(s) kept as data, e.g. "
                            f"{label(g.tables[k].file, g.tables[k].sheet)} row {i}")
    return problems


def filter_group(runtime: Runtime[ExploreContext], shop_dir: Path, g: explore.TableGroup,
                 name: str) -> tuple[set[R.RowId], list[str], dict]:
    """-> (rows to skip, subtotal regexes, trace record) of one table group."""
    found = R.find_rows(g)
    marked = set(found.header_rows)
    rec = {"table": name, "data_rows": len(found.data_rows), "repeated_headers": len(found.header_rows),
           "dense_columns": [g.first.columns[j] for j in found.dense], "candidates": found.candidates}
    regexes = []
    if found.patterns:
        prompt = filter_message(shop_dir, g, name, found)
        parsed, _, call = call_structured(runtime, verdict_schema(len(found.patterns)), [HumanMessage(prompt)])
        rec.update({"prompt": prompt, **call})
        for n, p in enumerate(found.patterns, 1):
            p.answer = getattr(parsed, f"group_{n}") if parsed is not None else None
            if p.answer == SUMMARY_ROW:
                marked.update(p.rows)
                if (rx := R.pattern_regex(p.key)) is not None:
                    regexes.append(rx)
        rec["patterns"] = [{"group": n, "key": p.key, "rows": len(p.rows), "answer": p.answer,
                            "examples": [f"{label(g.tables[k].file, g.tables[k].sheet)} row {i}"
                                         for k, i in R.examples(p)]}
                           for n, p in enumerate(found.patterns, 1)]
        rec["problems"] = _check_regexes(g, found, marked, regexes)
    rec["skipped"] = len(marked)
    rec["regexes"] = regexes
    return marked, regexes, rec


# ---------- the node ----------

def filter_rows(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    groups = line_table_candidates(shop_dir, state["header_rows"])
    skipped: dict[str, list[int]] = {}
    labels: dict[str, list[str]] = {}
    records, total = [], Counter()
    for g, name in zip(groups, option_names(groups)):
        marked, regexes, rec = filter_group(runtime, shop_dir, g, name)
        for k, t in enumerate(g.tables):
            rows = sorted(i for kk, i in marked if kk == k)
            if rows:
                skipped[explore.table_key(t.file, t.sheet)] = rows
        if regexes:
            labels[name] = regexes
        total.update(rec.get("usage", {}))
        records.append(rec)
    record = {"step": "filter_rows", "groups": records, "calls": sum("prompt" in r for r in records),
              "usage": dict(total), "seconds": since(start)}
    return {"skipped_rows": skipped, "subtotal_labels": labels, "trace": [record]}
