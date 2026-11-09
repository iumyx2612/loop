"""State of the explore_agent v1 graph (think ⇄ search, then write_plan → plan_check)."""
from typing import Annotated

from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan, ShopState


def add_new_notes(old: list[str], new: list[str]) -> list[str]:
    """Reducer for `notes`: append only the notes not already there (exact text, after stripping).
    Some models re-send all their earlier notes every round; operator.add would then repeat each note
    once per round and the prompts would keep growing."""
    seen = {n.strip() for n in old}
    out = list(old)
    for n in new:
        if n.strip() and n.strip() not in seen:
            seen.add(n.strip())
            out.append(n)
    return out


class ExploreAgentState(ShopState):
    """The outer loop, built like src/impls/qna: think and write_plan get a fresh prompt each call, rebuilt from
    this state, so there is no message list here; the search subgraph has its own (tool/state.py).
    shop_id, shop_dir, header_rows, readme, files and trace come from ShopState."""
    notes: Annotated[list[str], add_new_notes]  # think's findings; each round's notes appended, exact repeats dropped
    request: str  # think -> search: the one thing to look up next, overwritten each round ("" = think is done)
    report: str  # search -> think: this round's tool calls and outputs, overwritten each round
    rounds: int  # search rounds so far, to cap the loop
    plan: SalesLinePlan | None  # written by write_plan: the explore_agent's output, read by the analyst_agent
    plan_problems: list[str]  # what plan_check found wrong in the last plan; empty = passed
    plan_attempts: int  # plans checked so far, to cap the retry loop
    reply_problem: str  # the last think / write_plan reply could not be parsed; "" = fine
    bad_replies: int  # unparsable replies so far, to cap that retry
