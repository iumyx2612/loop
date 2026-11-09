"""Step 3's nodes: fill the sales_lines columns step 2 did not fill from the shop's other tables (orders, products,
customers, conversion tables).

The filter (fields_to_fill): step 3 works on every column step 2 asked about and did not map, i.e.
- open: the model answered "(not in this file)" (state["open_columns"]);
- failed: the model named a line column, but it failed its column check on every attempt, so step 2 left it
  neither mapped nor open (the reason is in state["column_problems"]). The data may still be in another table.
  A failed cross-column check is not counted: step 2 leaves those fields mapped, and they are line-only anyway.
Of these, only the columns another table can hold are kept. An orders or products table has one row per order or
per product, so it cannot say what one line holds: line-only columns are left for step 4 (missing) without asking.

The node (fill_from_tables), the user's snapshot workflow in its simplest form (explore_agent.md, step 3): for
every table group that is not the line tables, one structured call asks the same question as step 2 about the
fields to fill: which column of this table holds each, or "(not in this file)". The reply schema is
column_mapping.line_columns_schema on that table's columns, so names are real by construction. Each answer is
checked at once with step 2's column checks (checks.check_answer, on every table of the group): it passes → filled,
fails → failed (no retry). One table's result = its snapshot. The calls are independent: every table is asked about
every field from scratch.
Left out on purpose, to see the raw results first: the gate (3a: does the table attach to the lines?), roles (3b),
the merge (3d). by_field in the trace lists every table that claimed a field, so conflicts show up side by side.
"""
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ColumnSource, ExploreContext, TableRef
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.checks import check_answer
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.nodes import (ASKED, line_columns_schema,
                                                                               readme_meanings_text, table_text)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.prompts import COLUMN_MEANINGS, NOT_HERE
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.prompts import OTHER_TABLE
from src.datamate.analyst_agent.explore_agent.v2.find_line_tables.nodes import line_table_candidates, option_names
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured

# Columns another table can hold, by the kind of table that holds them
ORDER_FIELDS = ("order_date", "order_time", "status", "customer_name", "customer_group", "customer_code",
                "channel", "payment_method", "seller")  # an orders table, or a customers table joined to it
PRODUCT_FIELDS = ("product_code", "product_name", "product_group", "base_unit")  # a products / conversion table
FILLABLE = ORDER_FIELDS + PRODUCT_FIELDS
# Columns only the line holds:
# - order_id, unit, qty, unit_price, line_amount, line_discount: one value per line
# - cost_price: must be the cost written on the sale line (plan_check); a product list's cost is today's cost
# - qty_base: computed from a conversion table or the README list (SalesLinePlan.conversions), not mapped
LINE_ONLY = tuple(c for c in ASKED if c not in FILLABLE)
assert set(FILLABLE) <= set(ASKED), "every fillable column must be one step 2 asks"


def failed_columns(columns: list[ColumnSource], open_columns: list[str]) -> list[str]:
    """The asked columns step 2 neither mapped nor left open: their answer failed its column check."""
    done = {c.schema_column for c in columns} | set(open_columns)
    return [c for c in ASKED if c not in done]


def fields_to_fill(columns: list[ColumnSource], open_columns: list[str]) -> list[str]:
    """The columns step 3 works on (open or failed in step 2, and fillable from another table), in ASKED order;
    the others go to step 4 (missing)."""
    unfilled = set(open_columns) | set(failed_columns(columns, open_columns))
    return [c for c in ASKED if c in unfilled and c in FILLABLE]


# ---------- the snapshot workflow: one call per non-line table ----------

def named_groups(shop_dir: Path, headers: dict | None, line_tables: list[TableRef],
                 skipped: dict | None = None) -> list[tuple[str, explore.TableGroup, bool]]:
    """(name, group, is the line tables) of every table group with data rows; names as in step 1. The tables carry
    the filter step's rows (`skipped`), which their readers leave out."""
    groups = line_table_candidates(shop_dir, headers, skipped)
    line = {(r.file, r.sheet) for r in line_tables}
    return [(n, g, any((t.file, t.sheet) in line for t in g.tables)) for n, g in zip(option_names(groups), groups)]


def other_groups(shop_dir: Path, headers: dict | None, line_tables: list[TableRef],
                 skipped: dict | None = None) -> list[tuple[str, explore.TableGroup]]:
    """(name, group) of every table group with data rows that is not the line tables."""
    return [(n, g) for n, g, is_line in named_groups(shop_dir, headers, line_tables, skipped) if not is_line]


def table_message(shop_dir: Path, g: explore.TableGroup, fields: list[str]) -> str:
    rows = sum(len(t.data_numbers()) for t in g.tables)
    note = f", over {len(g.tables)} sheets / files with the same columns" if len(g.tables) > 1 else ""
    columns = "\n".join(f"- {f}: {COLUMN_MEANINGS[f]}" for f in fields)
    return OTHER_TABLE.format(readme_meanings=readme_meanings_text(shop_dir, g.first), rows=rows, tables_note=note,
                              table=table_text(g.first), columns=columns, not_here=NOT_HERE)


def table_snapshot(runtime: Runtime[ExploreContext], shop_dir: Path, name: str, g: explore.TableGroup,
                   fields: list[str]) -> dict:
    """One table's snapshot: ask, then check each answer. Record: table, prompt, call info, answer, filled
    (field -> column, passed its check), failed (field -> the check's problem). Used by the trial
    (fill_from_tables: every field) and by the snapshot node (snapshot/: only the fields of the table's anchors)."""
    prompt = table_message(shop_dir, g, fields)
    rec = {"table": name, "prompt": prompt, "filled": {}, "failed": {}}
    schema = line_columns_schema(g.first.columns, tuple(fields), name="TableColumns")
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
    except Exception as e:  # noqa: BLE001 - one table's failed call (e.g. a timeout) must not lose the others
        return {**rec, "call_failed": f"{type(e).__name__}: {str(e)[:500]}"}
    rec.update(call)
    if parsed is None:
        return rec
    rec["answer"] = parsed.model_dump()
    for f, column in rec["answer"].items():
        if column == NOT_HERE:
            continue
        problem, _ = check_answer(f, column, g.tables)
        if problem:
            rec["failed"][f] = problem
        else:
            rec["filled"][f] = column
    return rec


def fill_from_tables(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node for step 3 (snapshots only, no merge yet)."""
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    fields = fields_to_fill(state.get("columns") or [], state.get("open_columns") or [])
    record = {"step": "fill_from_tables", "fields": fields, "snapshots": []}
    if not state.get("line_tables"):
        record["error"] = "no line tables (step 1 chose none)"
    elif not fields:
        record["skipped"] = "nothing to fill: step 2 mapped or left line-only every column"
    else:
        for name, g in other_groups(shop_dir, state["header_rows"], state["line_tables"], state.get("skipped_rows")):
            record["snapshots"].append(table_snapshot(runtime, shop_dir, name, g, fields))
    # every table that claimed each field (passed or failed its check): the conflicts, side by side
    by_field = {f: [] for f in fields}
    for s in record["snapshots"]:
        for f, column in (s.get("answer") or {}).items():
            if column != NOT_HERE:
                by_field[f].append({"table": s["table"], "column": column, "passed": f in s["filled"]})
    usage = {}
    for s in record["snapshots"]:
        for k, v in s.get("usage", {}).items():
            usage[k] = usage.get(k, 0) + v
    record.update(by_field=by_field, usage=usage, seconds=since(start))
    snapshots = [{k: s[k] for k in ("table", "filled", "failed")} for s in record["snapshots"]]
    return {"fields_to_fill": fields, "snapshots": snapshots, "trace": [record]}
