"""The think node: structured output (ThinkOutput{notes, request}). Reads the README, the file list, its notes and
the new report; writes new notes and one request (-> search), or no request = done (-> write_plan). Fresh prompt
every round. Split from write_plan (2026-11-02): with the plan in think's output, Sonnet twice wrote "done" notes
and left both request and plan empty; and the plan's schema no longer rides along with every exploring round.
After a failed plan_check it sees the problems and either asks again (request) or notes the fix and is done.
An unparsable reply comes back here, at most MAX_BAD_REPLIES times (v1/utils.py).
"""
from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from src.datamate.analyst_agent.explore_agent.prompts import SCHEMA
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import cached, rulebook_sections
from src.datamate.analyst_agent.explore_agent.v1.state import ExploreAgentState
from src.datamate.analyst_agent.explore_agent.v1.think.prompts import LAST_ROUND, PROBLEMS, THINK_SYSTEM, THINK_USER
from src.datamate.analyst_agent.explore_agent.v1.think.state import ThinkOutput
from src.datamate.analyst_agent.explore_agent.v1.utils import (
    MAX_ROUNDS, NONE_YET, bad_reply, call_structured, notes_text, problems_text, reply_problem_text,
)


def think_system(context: ExploreContext) -> str:
    """think's system prompt: the same in every round (recorded once per run, not per step)."""
    return THINK_SYSTEM.format(schema=SCHEMA, rulebook=rulebook_sections(context.rulebook) or "(not given)")


def _messages(state: ExploreAgentState, context: ExploreContext) -> list:
    user = THINK_USER.format(
        shop_id=state["shop_id"], readme=state["readme"], files=state["files"], notes=notes_text(state),
        request=state["request"] or NONE_YET, report=state["report"] or NONE_YET,
        problems=problems_text(state, PROBLEMS), reply_problem=reply_problem_text(state),
        last_round=LAST_ROUND if state["rounds"] >= MAX_ROUNDS else "")
    return cached(think_system(context), user)


def think(state: ExploreAgentState, runtime: Runtime[ExploreContext]) -> Command[Literal["search", "write_plan", "think", "__end__"]]:
    """Turn the new report into notes, then ask for one more thing (-> search) or say it is done (-> write_plan)."""
    out, record = call_structured(runtime, ThinkOutput, _messages(state, runtime.context), "think",
                                  {"round": state["rounds"]})
    if out is None:
        return bad_reply(state, record, "think")
    record.update(notes=out.notes, request=out.request)
    update = {"notes": out.notes, "reply_problem": "", "trace": [record]}
    if out.request and state["rounds"] < MAX_ROUNDS:
        return Command(update={**update, "request": out.request}, goto="search")
    if out.request:  # a request after the last round: the plan is written from what it has
        record["error"] = "request after the last round; plan written from the notes"
    # Done: the next think (after a failed check) starts with no request and no report of its own
    return Command(update={**update, "request": "", "report": ""}, goto="write_plan")
