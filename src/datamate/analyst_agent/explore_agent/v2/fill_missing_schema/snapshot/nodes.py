"""snapshot: step 3 after attach / resolve_anchors (explore_agent.md "Step 3"; agreed with the user 2026-11-06).

For every attached table (state["attached"], round > 0; the lines are step 2's), one structured call asks the
fields its anchors look up: anchors.fields_for(the anchors of its links, the fields still to fill). The call is the
trial's (nodes.table_snapshot): the table's columns + "(not in this file)" as the enum, then step 2's column checks
on every table of the group (passed → filled, failed → failed; no retry), then the shared-column check over the
filled answers and the table's link columns (one retry of the fields on a shared column; 2026-11-11). A table with nothing to ask is skipped
without a call. Only the anchors it *attached by* count (its links), not every anchor find_anchors claimed for it.
A table is never asked the anchors it attached by (fix agreed 2026-11-06): ANCHOR_MAP has customer_code ↔
customer_name and product_code ↔ product_name, so a table linked by both would be asked its own join columns,
which only repeat the linked table's values (fake conflicts in biz_106 / 108 / 119 / 124).

Every table is asked on its own (agreed, option a): a field another table already filled is still asked, so the
candidates show up side by side and the field resolve (the next node) decides between them. The order of the
tables never changes an answer.

Output, state["snapshots"]: one entry per attached table that was asked, {"table", "round", "anchors", "asked",
"filled": {field: column}, "failed": {field: problem}}. With attach's links (state["attached"]) that is the plan's
`columns` (file / sheet = that table) and `joins`; nothing is written into `columns` until the resolve. The trace
record adds each call's prompt and reply, the skipped tables, `by_field` (every table claiming each field, passed or
not) and `unreached` (fields no attached table could be asked: no table attached by their anchor).
"""
import time
from pathlib import Path

from langgraph.runtime import Runtime

from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.prompts import NOT_HERE
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.anchors import ANCHOR_MAP, fields_for
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import shop_groups
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import fields_to_fill, table_snapshot
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState


def link_anchors(entry: dict) -> list[str]:
    """The anchors an attached table links by (its links' anchors, in ANCHOR_MAP order, no repeats)."""
    linked = {k["anchor"] for k in entry["links"]}
    return [a for a in ANCHOR_MAP if a in linked]


def snapshot(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node: one call per attached table, asked only its anchors' fields."""
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    fields = fields_to_fill(state.get("columns") or [], state.get("open_columns") or [])
    attached = state.get("attached") or []
    record = {"step": "snapshot", "fields": fields, "snapshots": [], "skipped_tables": []}
    if not attached:
        record.update(error="nothing attached (attach did not run)", seconds=since(start))
        return {"fields_to_fill": fields, "snapshots": [], "trace": [record]}
    if not fields:
        record.update(skipped="nothing to fill: step 2 mapped or left line-only every column", seconds=since(start))
        return {"fields_to_fill": fields, "snapshots": [], "trace": [record]}

    groups = shop_groups(state)
    for a in attached:
        if a["round"] == 0:  # the lines: step 2 mapped them
            continue
        anchors = link_anchors(a)
        # never its own link anchors: attach already proved that column holds the linked table's values, so it
        # only joins the tables (a customers table by code and name was asked both and "conflicted" with orders)
        asked = [f for f in fields_for(anchors, fields) if f not in anchors]
        if not asked:
            record["skipped_tables"].append({"table": a["table"], "anchors": anchors,
                                             "why": "its anchors look up no field still to fill"})
            continue
        # its join columns: no other field may sit there. An alias pair (attach._alias_links) links twice by one
        # anchor: the second column is kept as '<anchor> (other spelling)', so e.g. product_code on dev_011's
        # 'Ghi tắt' short forms is a shared column (retried, then failed by moved_onto_taken)
        linked = {}
        for k in a["links"]:
            linked[k["anchor"] if k["anchor"] not in linked else f"{k['anchor']} (other spelling)"] = k["column"]
        rec = table_snapshot(runtime, shop_dir, a["table"], groups[a["table"]], asked, linked)
        record["snapshots"].append({"round": a["round"], "anchors": anchors, "asked": asked, **rec})

    # every table that claimed each field (passed or failed its check): the candidates for the field resolve
    by_field = {f: [] for f in fields}
    for s in record["snapshots"]:
        for f, column in (s.get("answer") or {}).items():
            if column != NOT_HERE:
                by_field[f].append({"table": s["table"], "column": column, "passed": f in s["filled"]})
    asked_any = {f for s in record["snapshots"] for f in s["asked"]}
    usage = {}
    for s in record["snapshots"]:
        for k, v in s.get("usage", {}).items():
            usage[k] = usage.get(k, 0) + v
    record.update(by_field=by_field, unreached=[f for f in fields if f not in asked_any], usage=usage,
                  seconds=since(start))
    snapshots = [{k: s[k] for k in ("table", "round", "anchors", "asked", "filled", "failed")}
                 for s in record["snapshots"]]
    return {"fields_to_fill": fields, "snapshots": snapshots, "trace": [record]}
