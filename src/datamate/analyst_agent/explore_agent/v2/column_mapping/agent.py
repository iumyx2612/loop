"""Step 2 as a small agent: the column-mapping subgraph, and map_line_columns, the parent graph's node that runs it.

    START → answer ⇄ check → llm_check → shared_recheck → open_fields → END
    (check → answer while problems are left and attempts < MAX_ATTEMPTS)

- answer (LLM), check (code), llm_check (LLM, once), shared_recheck (LLM, one call per field on a column still
  shared; 2026-11-11) and open_fields (LLM, one call per open field with candidate columns; pass 2, 2026-11-11) are
  in nodes.py; check routes itself with Command(goto=...), so the edges are START → answer → check,
  llm_check → shared_recheck → open_fields → END.
- The subgraph has its own state (state.ColumnMappingState: the next call's request, the answer so far, the
  attempts). Each retry is a fresh self-contained call (nodes.retry_message), not a conversation.
  map_line_columns builds its input from the parent state, runs it, and returns the parent's fields
  (columns, open_columns, column_problems) plus one trace record. So the messages never reach the parent state.
  (Adding the compiled subgraph itself as a parent node would also
  work, but the subgraph would hand back the whole shared `trace` list and the parent's operator.add would append
  it a second time.)
- llm_check reviews the final matching once with a fresh message (no history); its record is the trace record's
  "llm_check", apart from the attempts.
"""
import time
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.nodes import (ASKED, MAX_ATTEMPTS, answer, check,
                                                                               first_message, line_tables_of,
                                                                               llm_check, open_fields, shared_recheck)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.state import ColumnMappingState
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState


def build_graph():
    g = StateGraph(ColumnMappingState, context_schema=ExploreContext)
    g.add_node("answer", answer)
    g.add_node("check", check)
    g.add_node("llm_check", llm_check)
    g.add_node("shared_recheck", shared_recheck)
    g.add_node("open_fields", open_fields)
    g.add_edge(START, "answer")
    g.add_edge("answer", "check")
    g.add_edge("llm_check", "shared_recheck")  # plain edges: the review and the two rechecks run once
    g.add_edge("shared_recheck", "open_fields")
    g.add_edge("open_fields", END)
    return g.compile()


column_mapping_agent = build_graph()
RECURSION_LIMIT = 2 * MAX_ATTEMPTS + 5  # answer + check per attempt, then llm_check, shared_recheck, open_fields


def _attempts(log: list[dict]) -> list[dict]:
    """The answer and check records of each attempt joined into one (attempt, fields, answer or error, problems,
    feedback, usage, stop_reason): the shape the runner's summary reads."""
    out: dict[int, dict] = {}
    for r in log:
        a = out.setdefault(r["attempt"], {"attempt": r["attempt"]})
        a.update({k: v for k, v in r.items() if k not in ("node", "checks")})
    return list(out.values())


def map_line_columns(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node for step 2."""
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    record = {"step": "map_line_columns", "attempts": []}
    refs = state.get("line_tables") or []
    skipped = state.get("skipped_rows") or {}
    tables = line_tables_of(shop_dir, refs, state["header_rows"], skipped)
    out = {}
    if not tables:
        record["error"] = "no line tables (step 1 chose none)"
    else:
        prompt = first_message(shop_dir, tables[0])
        record["prompt"] = prompt
        out = column_mapping_agent.invoke(
            {"shop_dir": str(shop_dir), "header_rows": state["header_rows"], "skipped_rows": skipped,
             "line_tables": refs,
             "raw_columns": tables[0].columns, "request": [HumanMessage(prompt)], "last_reply": "",
             "fields": list(ASKED),
             "answer": {}, "reply_error": "", "attempts": 0, "columns": [], "open_columns": [], "problems": [],
             "checks": {}, "log": []},
            context=runtime.context, config={"recursion_limit": RECURSION_LIMIT})
        record["attempts"] = _attempts([r for r in out["log"] if r["node"] in ("answer", "check")])
        record["llm_check"] = next(({k: v for k, v in r.items() if k != "node"}
                                    for r in out["log"] if r["node"] == "llm_check"), None)
        record["shared_recheck"] = next(({k: v for k, v in r.items() if k != "node"}
                                         for r in out["log"] if r["node"] == "shared_recheck"), None)
        record["open_fields"] = next(({k: v for k, v in r.items() if k != "node"}
                                      for r in out["log"] if r["node"] == "open_fields"), None)
        record["answer"] = out["answer"]
        if not out["answer"]:
            record["error"] = f"no readable reply in {MAX_ATTEMPTS} attempts"
    usage = {}
    for a in [*record["attempts"], record.get("llm_check") or {}, record.get("shared_recheck") or {},
              record.get("open_fields") or {}]:
        for k, v in a.get("usage", {}).items():
            usage[k] = usage.get(k, 0) + v
    columns, open_columns, problems = out.get("columns", []), out.get("open_columns", []), out.get("problems", [])
    record.update({"usage": usage, "checks": out.get("checks", {}),
                   "columns": {c.schema_column: c.raw_column for c in columns}, "open_columns": open_columns,
                   "problems": problems, "seconds": since(start)})
    return {"columns": columns, "open_columns": open_columns, "column_problems": problems, "trace": [record]}
