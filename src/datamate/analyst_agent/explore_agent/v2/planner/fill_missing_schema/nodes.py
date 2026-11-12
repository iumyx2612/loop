"""Step 3's nodes: fill the sales_lines columns step 2 did not fill from the shop's other tables (orders, products,
customers, conversion tables).

The filter (fields_to_fill): step 3 works on every column step 2 asked about and did not map, i.e.
- open: the model answered "(not in this file)" (state["open_columns"]);
- failed: the model named a line column, but it failed its column check on every attempt, so step 2 left it
  neither mapped nor open (the reason is in state["column_problems"]). The data may still be in another table.
  A failed cross-column check is not counted: step 2 leaves those fields mapped, and they are line-only anyway.
Of these, only the columns another table can hold are kept. An orders or products table has one row per order or
per product, so it cannot say what one line holds: line-only columns are left for step 4 (missing) without asking.

One table's snapshot (table_snapshot, called by the snapshot node, snapshot/): one structured call asks the same
question as step 2 about the fields to fill: which column of this table holds each, or "(not in this file)". The
reply schema is column_mapping.line_columns_schema on that table's columns, so names are real by construction. Each
answer is checked at once with step 2's column checks (checks.check_answer, on every table of the group): it
passes → filled, fails → failed (no retry). Then the shared-column check (2026-11-11): two answers on one column, or
an answer on the table's own link column, get one retry asking only those fields; a retried field that lands on a
column a field outside the retry holds is failed (moved_onto_taken).
"""
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ColumnSource, ExploreContext, TableRef
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.checks import SHARED_OK, check_answer, check_shared_columns
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import (ASKED, evidence_table,
                                                                               line_columns_schema, problems_text,
                                                                               readme_meanings_text, table_text)
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.prompts import COLUMN_MEANINGS, NOT_HERE
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.prompts import OTHER_TABLE, OTHER_TABLE_RETRY
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables.nodes import line_table_candidates, option_names
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


# ---------- the table groups, and one table's snapshot ----------

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


def check_snapshot(answer: dict[str, str], g: explore.TableGroup,
                   linked: dict[str, str]) -> tuple[dict, dict, list[str], tuple[str, ...], dict]:
    """Every check on one table's answer: (filled, failed, shared-column problems, the fields to ask again, the
    shared columns {column: fields}). Column checks as step 2 (check_answer, every table of the group). Shared
    columns (check_shared_columns, SHARED_OK pairs allowed) over the filled answers plus the table's link columns
    (`linked`: anchor -> column; attach proved they hold the anchor, so another field there is wrong). Only the
    asked fields on a shared column are asked again, never a link."""
    filled, failed = {}, {}
    for f, column in answer.items():
        if column == NOT_HERE:
            continue
        problem, _ = check_answer(f, column, g.tables)
        if problem:
            failed[f] = problem
        else:
            filled[f] = column
    problems, retry, shared = check_shared_columns({**linked, **filled})
    retry_fields = tuple(f for f in answer if f in retry and f not in linked)
    return filled, failed, problems, retry_fields, shared


def moved_onto_taken(filled: dict[str, str], retry_fields: tuple[str, ...],
                     taken: dict[str, str]) -> dict[str, str]:
    """After the snapshot retry: the retried fields whose new answer sits on a column a field outside the retry
    holds (`taken`: field -> column, the link columns and the first answer's filled fields that were not asked
    again) -> the problem. Option 1, agreed with the user 2026-11-11 (biz_109, 9B: the retry moved customer_group
    onto customer_name's 'Khách hàng' even with that column listed as known). The field outside the retry passed
    both rounds untouched, so the retried one is the wrong one; it is failed, the other stays. A share among
    retried fields only is not touched here (the check cannot tell which one is wrong; it stays filled)."""
    by_column: dict[str, list[str]] = {}
    for f, c in taken.items():
        by_column.setdefault(c, []).append(f)
    moved = {}
    for f in retry_fields:
        c = filled.get(f)
        others = by_column.get(c, []) if c else []
        if others and not any({f, *others} <= ok for ok in SHARED_OK):
            moved[f] = (f"after the retry, '{c}' was already held by {', '.join(others)} (not asked again), so this "
                        f"answer is the wrong one")
    return moved


def known_columns(linked: dict[str, str], filled: dict[str, str], retry_fields: tuple[str, ...]) -> dict[str, str]:
    """field -> column shown as known (not asked) in the snapshot retry: the table's link columns, then every filled
    field outside the retry that is related to a retried field (anchors.related_fields: same anchor family, not
    through order_id). Agreed with the user, 2026-11-11, after biz_109 on the 9B: the retry moved customer_group
    onto 'Khách hàng', which customer_name already held, because the prompt listed only the link column. A field
    being asked again is never listed (its column is the answer in question)."""
    # imported here: anchors imports this module (FILLABLE)
    from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.anchors import related_fields
    related = set().union(*(related_fields(f) for f in retry_fields))
    return {**linked, **{f: c for f, c in filled.items() if f in related and f not in retry_fields}}


def retry_snapshot_message(shop_dir: Path, g: explore.TableGroup, problems: list[str], shared: dict,
                           fields: tuple[str, ...], known: dict[str, str]) -> str:
    """`known`: field -> column listed as known, not asked (known_columns)."""
    rows = sum(len(t.data_numbers()) for t in g.tables)
    note = f", over {len(g.tables)} sheets / files with the same columns" if len(g.tables) > 1 else ""
    table_note, table = evidence_table(g.tables, shared)
    links = "".join(f"- '{c}' holds {f}\n" for f, c in known.items())
    linked_text = f"\n# Columns already known (not asked)\n{links}" if links else ""
    columns = "\n".join(f"- {f}: {COLUMN_MEANINGS[f]}" for f in fields)
    return OTHER_TABLE_RETRY.format(readme_meanings=readme_meanings_text(shop_dir, g.first), table_note=table_note,
                                    rows=rows, tables_note=note, table=table, linked=linked_text,
                                    problems=problems_text(problems), not_here=NOT_HERE, columns=columns)


def table_snapshot(runtime: Runtime[ExploreContext], shop_dir: Path, name: str, g: explore.TableGroup,
                   fields: list[str], linked: dict[str, str] | None = None) -> dict:
    """One table's snapshot: ask, then check the answer (check_snapshot). Two answers on one column (or an answer on
    a link column, `linked`: anchor -> column) → one retry asking only those fields (retry_snapshot_message);
    after it, a retried field now on a column a field outside the retry holds is failed (moved_onto_taken,
    `retry.moved_onto_taken`); a column still shared only among retried fields leaves its fields filled, as step 2
    does (the check cannot tell which one is wrong), and the problem stays in the record (`shared_left`). Record: table, prompt, call info, answer (with the
    retry's answers), filled (field -> column, passed its checks), failed (field -> the column check's problem),
    usage (both calls), and `retry` when there was one. Used by the snapshot node (snapshot/: only the fields of
    the table's anchors)."""
    linked = linked or {}
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
    filled, failed, problems, retry_fields, shared = check_snapshot(rec["answer"], g, linked)
    if retry_fields:
        known = known_columns(linked, filled, retry_fields)
        retry = {"problems": problems, "fields": list(retry_fields), "first_answer": dict(rec["answer"]),
                 "known": known}
        retry["prompt"] = retry_snapshot_message(shop_dir, g, problems, shared, retry_fields, known)
        schema = line_columns_schema(g.first.columns, retry_fields, name="TableColumns")
        try:
            parsed, _, call = call_structured(runtime, schema, [HumanMessage(retry["prompt"])])
        except Exception as e:  # noqa: BLE001 - a failed retry keeps the first answer's result
            parsed, call = None, {"call_failed": f"{type(e).__name__}: {str(e)[:500]}"}
        retry.update(call)
        if parsed is not None:
            retry["answer"] = parsed.model_dump()
            taken = {**linked, **{f: c for f, c in filled.items() if f not in retry_fields}}
            rec["answer"] = {**rec["answer"], **retry["answer"]}
            filled, failed, problems, _, shared = check_snapshot(rec["answer"], g, linked)
            moved = moved_onto_taken(filled, retry_fields, taken)
            if moved:  # failed, then the shares checked again without them
                retry["moved_onto_taken"] = moved
                failed.update(moved)
                filled = {f: c for f, c in filled.items() if f not in moved}
                problems, _, shared = check_shared_columns({**linked, **filled})
        if call.get("usage"):
            rec["usage"] = {k: rec.get("usage", {}).get(k, 0) + v for k, v in call["usage"].items()}
        rec["retry"] = retry
    if problems:
        rec["shared_left"] = problems
    rec.update(filled=filled, failed=failed)
    return rec
