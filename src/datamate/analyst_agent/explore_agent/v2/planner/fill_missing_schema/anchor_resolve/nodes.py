"""resolve_anchors: find_anchors' part c, the anchor resolve (the user's idea; explore_agent.md "Step 3: agreed
structure", 2c). Runs after attach.

attach keeps every table whose column is a true anchor (it shares the values and is unique), but cannot tell the
kind of table: a payments book with one payment per order looks exactly like the orders table. So for each anchor
that more than one table attached by, one structured call shows the candidates side by side (table name, its
anchor column, header + 5 rows) and asks which one is the shop's own list of orders / products / customers. The
reply is an enum of the candidate names (step 1's shape).

Pick one, no merge (agreed for now): the other candidates' claims for that anchor are dropped
(state["dropped_claims"]), and attach_tables runs again without them, so a dropped table takes its chain with it
and a table kept for one anchor but dropped for another keeps only the kept link. A call that fails or cannot be
read drops nothing for that anchor (the trace says so).
"""
import time
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import table_text
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.prompts import COLUMN_MEANINGS
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.anchor_resolve.prompts import (CHOOSE_SOURCE,
                                                                                                    SUBJECTS)
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.anchors import ANCHOR_FIELDS
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.attach import (MIN_UNIQUE, attach_tables,
                                                                                    shop_groups)
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured


def conflicts(attached: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """anchor -> [(table, its anchor column)] for every anchor more than one attached table links by (the lines,
    round 0, are never a candidate), in ANCHOR_FIELDS order."""
    out = {}
    for f in ANCHOR_FIELDS:
        cands = [(a["table"], k["column"]) for a in attached if a["round"] > 0 for k in a["links"] if k["anchor"] == f]
        if len(cands) > 1:
            out[f] = cands
    return out


def source_message(anchor: str, cands: list[tuple[str, str]], groups: dict[str, explore.TableGroup]) -> str:
    options = []
    for name, column in cands:
        g = groups[name]
        rows = sum(len(t.data_numbers()) for t in g.tables)
        options.append(f"Option: {name}\n  {rows:,} rows below the header; its {anchor} column: '{column}'\n"
                       f"{table_text(g.first)}")
    return CHOOSE_SOURCE.format(anchor=anchor, meaning=COLUMN_MEANINGS[anchor], subject=SUBJECTS[anchor],
                                options="\n\n".join(options))


def choose_source(runtime: Runtime[ExploreContext], anchor: str, cands: list[tuple[str, str]],
                  groups: dict[str, explore.TableGroup]) -> dict:
    """One anchor's call. Record: anchor, candidates, prompt, call info, chosen (None when no readable reply)."""
    names = [n for n, _ in cands]
    prompt = source_message(anchor, cands, groups)
    rec = {"anchor": anchor, "candidates": [{"table": n, "column": c} for n, c in cands], "prompt": prompt,
           "chosen": None}
    schema = create_model("AnchorSource", table=(Literal[tuple(names)],
                                                 Field(description='the option\'s name exactly as written after '
                                                                   '"Option:"')))
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
    except Exception as e:  # noqa: BLE001 - a failed call drops nothing; the other anchors go on
        return {**rec, "call_failed": f"{type(e).__name__}: {str(e)[:500]}"}
    rec.update(call)
    if parsed is not None:
        rec["chosen"] = parsed.table
    return rec


def resolve_anchors(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node: one call per anchor with several candidates, then attach again."""
    start = time.perf_counter()
    record = {"step": "resolve_anchors", "choices": []}
    attached = state.get("attached") or []
    if not attached:
        record.update(skipped="nothing attached (attach did not run)", seconds=since(start))
        return {"trace": [record]}
    found = conflicts(attached)
    if not found:
        record.update(skipped="no anchor with several tables: nothing to choose", seconds=since(start))
        return {"dropped_claims": [], "trace": [record]}
    groups = shop_groups(state)
    dropped = []
    for anchor, cands in found.items():
        rec = choose_source(runtime, anchor, cands, groups)
        if rec["chosen"]:
            rec["dropped"] = [n for n, _ in cands if n != rec["chosen"]]
            dropped += [{"table": n, "anchor": anchor} for n in rec["dropped"]]
        record["choices"].append(rec)
    min_unique = state.get("min_unique") or MIN_UNIQUE
    attached, left_out = attach_tables(state["anchor_tables"], groups, min_unique, dropped)
    usage = {}
    for c in record["choices"]:
        for k, v in c.get("usage", {}).items():
            usage[k] = usage.get(k, 0) + v
    record.update(min_unique=min_unique, dropped_claims=dropped, attached=attached, left_out=left_out, usage=usage,
                  seconds=since(start))
    return {"dropped_claims": dropped, "attached": attached, "trace": [record]}
