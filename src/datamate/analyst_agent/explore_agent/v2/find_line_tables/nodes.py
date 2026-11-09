"""Step 1, choose_line_tables: which table group holds the sales lines.

- The options are the table groups of explore.table_groups (tables with the same header: month sheets, split
  files), so choosing one option gives every table of the line data at once. Groups without data rows are left out.
- Only one option: chosen without an LLM call.
- Otherwise one structured-output call. The reply schema is built per shop: one field, `option`, whose type is a
  Literal of this shop's option names, so the reply can only be one of them (an invented or misspelled name fails
  to parse instead of being accepted).
- No retry yet: an unparsable reply leaves line_tables = [] and the error in the trace.
"""
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ExploreContext, TableRef
from src.datamate.analyst_agent.explore_agent.utils import label, since
from src.datamate.analyst_agent.explore_agent.v2.find_line_tables.prompts import LINE_TABLES
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured

SAMPLE_ROWS = 3  # data rows shown per option


def line_table_candidates(shop_dir: Path, headers: dict | None,
                          skipped: dict | None = None) -> list[explore.TableGroup]:
    """The table groups that have at least one data row below the header. `skipped` (state["skipped_rows"]): the
    tables carry the filter step's rows, and every reader of their data_rows leaves them out."""
    return [g for g in explore.table_groups(shop_dir, headers, skipped) if any(t.data_numbers() for t in g.tables)]


def _group_name(g: explore.TableGroup) -> str:
    """'X.xlsx [S]' for one table, 'X.xlsx (17 sheets)', 'X_1.xlsx + X_2026.xlsx (19 sheets)', 'a_1.csv + a_2.csv'."""
    tables = g.tables
    if len(tables) == 1:
        return label(tables[0].file, tables[0].sheet)
    files = list(dict.fromkeys(t.file for t in tables))
    n = sum(t.sheet is not None for t in tables)
    word = "tables" if files[0].lower().endswith(".sqlite") else "sheets"
    return " + ".join(files) + (f" ({n} {word})" if n else "")


def option_names(groups: list[explore.TableGroup]) -> list[str]:
    """One unique name per group. Two groups with the same name (e.g. two sets of month sheets in one file) get
    their first table added."""
    names = [_group_name(g) for g in groups]
    counts = Counter(names)
    return [f"{n}, first {label(g.first.file, g.first.sheet)}" if counts[n] > 1 else n
            for n, g in zip(names, groups)]


def _option_text(g: explore.TableGroup, name: str) -> str:
    t = g.first
    data_rows = sum(len(m.data_numbers()) for m in g.tables)
    labels = [label(m.file, m.sheet) for m in g.tables]
    listed = ", ".join(labels) if len(labels) <= 3 else f"{labels[0]} … {labels[-1]}"
    lines = [f"Option: {name}",
             f"  {len(labels)} table(s), {data_rows:,} rows below the header: {listed}",
             f"  columns: {' | '.join(t.columns)}",
             f"  first rows of {labels[0]} (text cells in quotes):"]
    # explore's own row format, so the cells look the same as in every other explore output
    lines += ["  " + explore._row_line(i, t.rows[i - 1]) for i in t.data_numbers()[:SAMPLE_ROWS]]
    return "\n".join(lines)


def options_text(groups: list[explore.TableGroup], names: list[str]) -> str:
    return "\n\n".join(_option_text(g, n) for g, n in zip(groups, names))


def line_tables_schema(names: list[str]) -> type[BaseModel]:
    """The reply schema for one shop: `option` may only be one of `names` (a JSON-schema enum)."""
    return create_model("LineTablesChoice",
                        option=(Literal[tuple(names)],
                                Field(description='the option\'s name exactly as written after "Option:"')))


def choose_line_tables(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    groups = line_table_candidates(shop_dir, state["header_rows"], state.get("skipped_rows"))
    names = option_names(groups)
    record = {"step": "choose_line_tables", "options": names}
    chosen = None
    if not groups:
        record["error"] = "no table has rows below its header"
    elif len(groups) == 1:
        chosen = groups[0]
        record["skipped"] = "only one option, no LLM call"
    else:
        prompt = LINE_TABLES.format(readme=state["readme"], options=options_text(groups, names))
        parsed, _, call = call_structured(runtime, line_tables_schema(names), [HumanMessage(prompt)])
        record.update({"prompt": prompt, **call})
        if parsed is not None:
            record["answer"] = parsed.option
            chosen = groups[names.index(parsed.option)]
    line_tables = [TableRef(file=t.file, sheet=t.sheet) for t in chosen.tables] if chosen else []
    record["line_tables"] = [label(r.file, r.sheet) for r in line_tables]
    record["seconds"] = since(start)
    return {"line_tables": line_tables, "trace": [record]}
