"""The v1 graph's own nodes, besides think (think/) and write_plan (write_plan/):

- prepare: the shared prepare (README + file list) + the loop's starting values (notes, request, rounds, ...).
- search: runs the search subgraph (tool/) on think's request; its tool calls and outputs become the report.
- plan_check: no LLM (explore_agent/plan_check.py). Passed -> END; problems -> back to think, which either asks the
  search agent again or says how to fix them and is done (-> write_plan, which sees the problems).
"""
import time
from collections import Counter
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command

from src.datamate.analyst_agent.explore_agent import nodes as shared
from src.datamate.analyst_agent.explore_agent.plan_check import check_plan
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import since, usage
from src.datamate.analyst_agent.explore_agent.v1.state import ExploreAgentState
from src.datamate.analyst_agent.explore_agent.v1.tool import format_report, run_search, transcript
from src.datamate.analyst_agent.explore_agent.v1.utils import MAX_PLAN_ATTEMPTS

# The loop's fields at the start of a run (set by prepare)
LOOP_START = {"notes": [], "request": "", "report": "", "rounds": 0, "plan": None, "plan_problems": [],
              "plan_attempts": 0, "reply_problem": "", "bad_replies": 0}


def prepare(state: ExploreAgentState) -> dict:
    """The shared prepare (README + file list), plus the loop's starting values."""
    return {**shared.prepare(state), **LOOP_START}


def search(state: ExploreAgentState, runtime: Runtime[ExploreContext]) -> Command[Literal["think"]]:
    """Run the search subgraph on think's request and hand the report back to think."""
    start = time.perf_counter()
    messages, cut = run_search(state["request"], state["files"], state["shop_dir"], state["header_rows"],
                               runtime.context)
    total = Counter()
    for m in messages:
        if isinstance(m, AIMessage):
            total.update(usage(m))
    calls = [{"name": c["name"], "args": c["args"]} for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
    report = format_report(messages)
    # transcript: every LLM turn and tool output (errors included); report: exactly what think gets next round
    record = {"step": "search", "round": state["rounds"], "seconds": since(start), "request": state["request"],
              "calls": calls, "cut_by_limit": cut, "transcript": transcript(messages), "report": report,
              "usage": dict(total)}
    return Command(update={"report": report, "rounds": state["rounds"] + 1, "trace": [record]}, goto="think")


def plan_check(state: ExploreAgentState) -> Command[Literal["think", "__end__"]]:
    """Check the plan in code. Passed, or no attempts left: END. Otherwise back to think with the problems: it
    decides whether fixing them needs another look at the files (request) or not (done -> write_plan again)."""
    start = time.perf_counter()
    problems = check_plan(Path(state["shop_dir"]), state["plan"], state["header_rows"])
    attempts = state["plan_attempts"] + 1
    record = {"step": "plan_check", "attempt": attempts, "seconds": since(start), "problems": problems}
    done = not problems or attempts >= MAX_PLAN_ATTEMPTS
    return Command(update={"plan_problems": problems, "plan_attempts": attempts, "trace": [record]},
                   goto=END if done else "think")
