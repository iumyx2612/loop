"""The write_plan node: structured output (SalesLinePlan) from the README, the file list and think's notes (+ the
last check's problems and plan). No tools: what the notes lack goes to missing_columns. -> plan_check.
An unparsable reply comes back here, at most MAX_BAD_REPLIES times (v1/utils.py).
"""
from typing import Literal

from langgraph.runtime import Runtime
from langgraph.types import Command

from src.datamate.analyst_agent.explore_agent.prompts import SCHEMA
from src.datamate.analyst_agent.explore_agent.state import ExploreContext, SalesLinePlan
from src.datamate.analyst_agent.explore_agent.utils import cached, rulebook_sections
from src.datamate.analyst_agent.explore_agent.v1.state import ExploreAgentState
from src.datamate.analyst_agent.explore_agent.v1.utils import (
    bad_reply, call_structured, notes_text, problems_text, reply_problem_text,
)
from src.datamate.analyst_agent.explore_agent.v1.write_plan.prompts import (
    PLAN_PROBLEMS, WRITE_PLAN_SYSTEM, WRITE_PLAN_USER,
)


def write_plan_system(context: ExploreContext) -> str:
    """write_plan's system prompt: the same in every call (recorded once per run, not per step)."""
    return WRITE_PLAN_SYSTEM.format(schema=SCHEMA, rulebook=rulebook_sections(context.rulebook) or "(not given)")


def _messages(state: ExploreAgentState, context: ExploreContext) -> list:
    user = WRITE_PLAN_USER.format(
        shop_id=state["shop_id"], readme=state["readme"], files=state["files"], notes=notes_text(state),
        problems=problems_text(state, PLAN_PROBLEMS) + reply_problem_text(state))
    return cached(write_plan_system(context), user)


def write_plan(state: ExploreAgentState, runtime: Runtime[ExploreContext]) -> Command[Literal["plan_check", "write_plan", "__end__"]]:
    """Fill in the whole SalesLinePlan from the README, the file list and think's notes (+ the last check's problems)."""
    plan, record = call_structured(runtime, SalesLinePlan, _messages(state, runtime.context), "write_plan",
                                   {"attempt": state["plan_attempts"] + 1})
    if plan is None:
        return bad_reply(state, record, "write_plan")
    record["plan"] = plan.model_dump()
    return Command(update={"plan": plan, "reply_problem": "", "trace": [record]}, goto="plan_check")
