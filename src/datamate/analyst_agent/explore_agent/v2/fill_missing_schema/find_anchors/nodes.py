"""find_anchors, part a: the loop (explore_agent.md, "Step 3: agreed structure").

Table by table: one structured call per non-line table group asks, for each anchor (anchors.ANCHOR_FIELDS), which
of the table's columns holds it, or "(not in this file)". The reply schema is column_mapping.line_columns_schema
on that table's columns, so a name is always real. The line tables need no call: their anchors are the ones step 2
mapped.

Not yet (parts b and c): the value check (a claim sharing no values with the same anchor elsewhere is dropped) and
the anchor resolve (several tables left as the source of one anchor -> the LLM sees them side by side). Every claim
is kept as the model gave it.

Output, state["anchor_tables"]: one entry per table group, {"table": name, "line": bool, "anchors": {anchor:
column}}; the trace record also has each call's prompt, reply and usage, and `by_anchor` (every table claiming each
anchor).
"""
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.nodes import (line_columns_schema,
                                                                               readme_meanings_text, table_text)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.prompts import COLUMN_MEANINGS, NOT_HERE
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.anchors import ANCHOR_FIELDS
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.find_anchors.prompts import TABLE_ANCHORS
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import fields_to_fill, named_groups
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured


def anchors_message(shop_dir: Path, g: explore.TableGroup) -> str:
    rows = sum(len(t.data_numbers()) for t in g.tables)
    note = f", over {len(g.tables)} sheets / files with the same columns" if len(g.tables) > 1 else ""
    anchors = "\n".join(f"- {a}: {COLUMN_MEANINGS[a]}" for a in ANCHOR_FIELDS)
    return TABLE_ANCHORS.format(readme_meanings=readme_meanings_text(shop_dir, g.first), rows=rows, tables_note=note,
                                table=table_text(g.first), anchors=anchors, not_here=NOT_HERE)


def table_anchors(runtime: Runtime[ExploreContext], shop_dir: Path, name: str, g: explore.TableGroup) -> dict:
    """One table's call. Record: table, prompt, call info (usage, stop reason; error + raw reply when unreadable),
    the reply, and anchors (the claimed ones only)."""
    prompt = anchors_message(shop_dir, g)
    rec = {"table": name, "line": False, "prompt": prompt, "anchors": {}}
    schema = line_columns_schema(g.first.columns, ANCHOR_FIELDS, name="TableAnchors")
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
    except Exception as e:  # noqa: BLE001 - one table's failed call (e.g. a timeout) must not lose the others
        return {**rec, "call_failed": f"{type(e).__name__}: {str(e)[:500]}"}
    rec.update(call)
    if parsed is not None:
        rec["reply"] = parsed.model_dump()
        rec["anchors"] = {a: c for a, c in rec["reply"].items() if c != NOT_HERE}
    return rec


def find_anchors(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node (for now: part a only)."""
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    record = {"step": "find_anchors", "tables": []}
    if not state.get("line_tables"):
        record["error"] = "no line tables (step 1 chose none)"
    elif not fields_to_fill(state.get("columns") or [], state.get("open_columns") or []):
        record["skipped"] = "nothing to fill: no other table is needed"
    else:
        mapped = {c.schema_column: c.raw_column for c in state.get("columns") or []}
        for name, g, is_line in named_groups(shop_dir, state["header_rows"], state["line_tables"],
                                             state.get("skipped_rows")):
            if is_line:  # the line's anchors are step 2's mapping
                record["tables"].append({"table": name, "line": True, "from": "step 2",
                                         "anchors": {a: mapped[a] for a in ANCHOR_FIELDS if a in mapped}})
            else:
                record["tables"].append(table_anchors(runtime, shop_dir, name, g))
    by_anchor = {a: [{"table": t["table"], "column": t["anchors"][a], "line": t["line"]}
                     for t in record["tables"] if a in t["anchors"]] for a in ANCHOR_FIELDS}
    usage = {}
    for t in record["tables"]:
        for k, v in t.get("usage", {}).items():
            usage[k] = usage.get(k, 0) + v
    record.update(by_anchor=by_anchor, usage=usage, seconds=since(start))
    entries = [{k: t[k] for k in ("table", "line", "anchors")} for t in record["tables"]]
    return {"anchor_tables": entries, "trace": [record]}
