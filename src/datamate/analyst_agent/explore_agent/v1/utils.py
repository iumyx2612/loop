"""What think and write_plan share: the loop's caps, one structured-output call with its trace record, the retry
after an unparsable reply, and the prompt parts both show (notes, the last plan's problems, the reply problem)."""
import time

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command

from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import raw_text, since, usage
from src.datamate.analyst_agent.explore_agent.v1.prompts import REPLY_PROBLEM
from src.datamate.analyst_agent.explore_agent.v1.state import ExploreAgentState

MAX_ROUNDS = 20  # search rounds; at the cap think is told to finish, and the plan is written from its notes
MAX_PLAN_ATTEMPTS = 3  # plans checked; after the last one the plan is kept with its problems
MAX_BAD_REPLIES = 3  # unparsable think / write_plan replies before giving up
NONE_YET = "(none yet)"


def notes_text(state: ExploreAgentState) -> str:
    return "\n".join(f"- {n}" for n in state["notes"]) or NONE_YET


def problems_text(state: ExploreAgentState, template: str) -> str:
    """The last plan's problems + that plan, in think's or write_plan's template; "" if none."""
    if not state["plan_problems"]:
        return ""
    return template.format(problems="\n".join(f"- {p}" for p in state["plan_problems"]),
                           plan=state["plan"].model_dump_json(indent=1) if state["plan"] else "(none)")


def reply_problem_text(state: ExploreAgentState) -> str:
    return REPLY_PROBLEM.format(problem=state["reply_problem"]) if state["reply_problem"] else ""


def call_structured(runtime: Runtime[ExploreContext], schema, messages: list, step: str, extra: dict) -> tuple:
    """One structured-output call -> (parsed or None, trace record). include_raw: also returns the raw AIMessage
    (for token usage) and a parsing error instead of raising."""
    start = time.perf_counter()
    llm = runtime.context.model.with_structured_output(schema, method="json_schema", include_raw=True)
    result = llm.invoke(messages)
    raw = result["raw"]
    # stop_reason: why the reply ended (end_turn, max_tokens, refusal, ...), to tell a cut reply from a short one;
    # prompt = the user message only (the system prompt is the same every call: see think_system / write_plan_system)
    record = {"step": step, **extra, "seconds": since(start), "usage": usage(raw),
              "stop_reason": (getattr(raw, "response_metadata", None) or {}).get("stop_reason"),
              "prompt": messages[-1].content}
    if result["parsed"] is None:
        record["error"] = str(result.get("parsing_error"))[:500]
        record["raw_reply"] = raw_text(raw)
    return result["parsed"], record


def bad_reply(state: ExploreAgentState, record: dict, node: str) -> Command:
    """An unparsable reply: back to the same node with the problem, until MAX_BAD_REPLIES; then END."""
    bad = state["bad_replies"] + 1
    record["bad_replies"] = bad
    problem = f"It did not match the output format ({record['error'][:200]}). Reply in the output format."
    return Command(update={"reply_problem": problem, "bad_replies": bad, "trace": [record]},
                   goto=END if bad >= MAX_BAD_REPLIES else node)
