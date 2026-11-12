"""Step 6, cleaning_inputs: the plan's cleaning inputs that code can decide alone (plain code, no LLM; hybrid agreed
with the user, 2026-11-10: what needs no LLM is pure code). Runs after map_statuses: step 5 does not need filled
data (forward fill only copies a word already on the order's first line), and its leftover-blank rule assumes what
6a does. Runs after map_line_kinds (step 7, agreed 2026-11-11): 6c leaves step 7's non_revenue_names rows out.

Order: 6b return lines (return_lines.py) → state["return_lines"]; 6a forward fill (forward_fill.py, whose
per-order check leaves 6b's return lines out) → state["forward_fill_columns"]; 6c the duplicate lines within an
order after forward fill, not-revenue rows left out (duplicates.py) → a line in state["notes"] when there are any.
"""
import time
from pathlib import Path

from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.duplicates import duplicates
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.duplicates import note as duplicates_note
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.forward_fill import forward_fill
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.forward_fill import problems as ff_problems
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.return_lines import problems as rl_problems
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs.return_lines import return_lines
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import line_tables_of
from src.datamate.analyst_agent.explore_agent.v2.planner.line_kinds.candidates import products_table_names
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState


def cleaning_inputs(state: WorkflowState) -> dict:
    """The parent graph's node."""
    start = time.perf_counter()
    record = {"step": "cleaning_inputs"}
    columns = {c.schema_column: c.raw_column for c in state.get("columns") or []}
    ff_columns: list[str] = []
    has_returns = False
    notes: list[str] = []
    if not state.get("line_tables"):
        record["return_lines"] = record["forward_fill"] = record["duplicates"] = {
            "skipped": "no line tables (step 1 chose none)"}
    else:
        tables = line_tables_of(Path(state["shop_dir"]), state["line_tables"], state["header_rows"],
                                state.get("skipped_rows"))
        rl = return_lines(tables, columns)
        has_returns = rl.return_lines
        record["return_lines"] = {
            "qty_col": rl.qty_col, "amount_col": rl.amount_col, "return_lines": rl.return_lines, "count": rl.count,
            "examples": rl.examples, "skipped": rl.skipped, "problems": rl_problems(rl)}
        ff = forward_fill(tables, columns, rl.rows)
        ff_columns = ff.columns
        record["forward_fill"] = {
            "order_col": ff.order_col, "columns": ff.columns, "rows": ff.rows, "continuation": ff.continuation,
            "orphans": ff.orphans, "fields": ff.fields, "blank_on_continuation": ff.blank_on_continuation,
            "left_out": ff.left_out, "conflicts": ff.conflicts, "return_only": ff.return_only,
            "skipped": ff.skipped, "problems": ff_problems(ff)}
        not_revenue = state.get("non_revenue_names") or []
        names_by_code = {}
        if not_revenue and "product_name" not in columns:  # code-only shop: step 7 named the rows by the products table
            names_by_code = products_table_names(state)[1]
        dup = duplicates(tables, ff.order_col, ff.columns, columns.get("product_name"), columns.get("product_code"),
                         not_revenue, names_by_code)
        record["duplicates"] = {"order_col": dup.order_col, "count": dup.count, "orders": dup.orders,
                                "examples": dup.examples, "not_revenue_rows": dup.not_revenue_rows,
                                "skipped": dup.skipped}
        if n := duplicates_note(dup):
            notes.append(n)
    record["notes"] = notes
    record["seconds"] = since(start)
    return {"forward_fill_columns": ff_columns, "return_lines": has_returns, "notes": notes, "trace": [record]}
