"""The query_agent (answers questions): agent ⇄ tools → finalize.

The agent node lets the LLM call find_entity / information_finder / calculate until it replies without tool
calls; finalize turns that reply into a §9 submission with structured output.
"""
import datetime as dt
import json
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.datamate.query_agent.prompts import FINALIZE, SYSTEM, USER
from src.datamate.query_agent.state import QueryState
from src.datamate.query_agent.tools import TOOLS, _shop

OUT_OF_SCOPE = "OUT OF SCOPE"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# What the agent can and cannot ask of a shop, by the column it needs
FEATURES = {
    "order_id": "orders (order counts, cancellations, per-order questions)",
    "product_key": "products (product names, groups, quantities sold)",
    "customer_key": "customers", "customer_group": "customer groups", "product_group": "product groups",
    "channel": "sales channel", "payment_method": "payment method", "seller": "seller",
    "order_time": "order time (needed for shifts)",
    "cost_price": "cost price on sales lines (needed for lãi gộp)",
}


@dataclass
class QueryContext:
    """Fixed for the whole run and passed at invoke time; nodes read it via runtime.context."""

    model: BaseChatModel
    rulebook: str


def shop_brief(shop_dir: str) -> str:
    shop = _shop(shop_dir)
    m = shop.manifest
    lines = [f"- data from {m['date_min']} to {m['data_end']}"]
    missing = [label for col, label in FEATURES.items() if not shop.has(col)]
    lines.append("- not recorded (questions needing these are unanswerable): " + (", ".join(missing) or "nothing"))
    for col, label in (("product_group", "product groups"), ("customer_group", "customer groups")):
        if shop.has(col):
            lines.append(f"- {label}: " + ", ".join(sorted(shop.lines[col].dropna().unique())))
    lines += [f"- {note}" for note in m.get("brief", [])]  # how this shop's records map onto sales lines
    return "\n".join(lines)


def _opening_messages(state: QueryState, context: QueryContext) -> list:
    """System prompt (cached: identical for every question) and the question; rebuilt on each call."""
    as_of = dt.date.fromisoformat(state["as_of_date"])
    system = SystemMessage(content=[{"type": "text", "text": SYSTEM.format(rulebook=context.rulebook),
                                     "cache_control": {"type": "ephemeral"}}])
    return [system, HumanMessage(USER.format(question=state["question"], as_of_date=state["as_of_date"],
                                             weekday=WEEKDAYS[as_of.weekday()], shop_brief=shop_brief(state["shop_dir"])))]


def agent(state: QueryState, runtime: Runtime[QueryContext]) -> Command[Literal["tools", "finalize"]]:
    """Let the LLM pick the next calls; once it replies without tool calls it has concluded."""
    llm = runtime.context.model.bind_tools(TOOLS)
    response = llm.invoke([*_opening_messages(state, runtime.context), *state["messages"]])
    return Command(update={"messages": [response]}, goto="tools" if response.tool_calls else "finalize")


class Submission(BaseModel):
    type: Literal["number", "percent", "date", "boolean", "string", "list", "table", "unanswerable", "abstain"] = Field(
        description="The answer type from rulebook §9, or abstain.")
    answer_json: str = Field(default="", description="The answer value as JSON text, e.g. 89526400, 12.4, "
                             "\"2026-08-15\", true, \"Sting dâu 330ml\", [\"A\", \"B\"]. Empty for unanswerable / abstain.")
    reason: str = Field(default="", description="Only for unanswerable: the NO DATA message.")


def _no_data_seen(messages) -> bool:
    return any(isinstance(m, ToolMessage) and str(m.content).startswith("NO DATA") for m in messages)


def finalize(state: QueryState, runtime: Runtime[QueryContext]) -> dict:
    """Convert the agent's final reply into a submission dict in the rulebook's format."""
    if OUT_OF_SCOPE in str(state["messages"][-1].content):
        return {"submission": {"type": "abstain"}}
    # json_schema: the provider's native structured output (forced tool calling is unsupported on claude-sonnet-5-5)
    llm = runtime.context.model.with_structured_output(Submission, method="json_schema")
    out = llm.invoke([*_opening_messages(state, runtime.context), *state["messages"], HumanMessage(FINALIZE)])

    if out.type == "abstain":
        return {"submission": {"type": "abstain"}}
    if out.type == "unanswerable":
        # −1 if wrong: only trust it when a tool really reported missing data
        if not _no_data_seen(state["messages"]):
            return {"submission": {"type": "abstain"}}
        return {"submission": {"type": "unanswerable", "reason": out.reason or "no data"}}
    try:
        answer = json.loads(out.answer_json)
    except json.JSONDecodeError:
        answer = out.answer_json  # not valid JSON: a bare string answer is the only safe reading
    if (out.type in ("number", "percent") and not isinstance(answer, (int, float))) or \
            (isinstance(answer, bool) and out.type != "boolean"):
        return {"submission": {"type": "abstain"}}  # malformed number: abstaining scores 0, a guess −1
    if isinstance(answer, float) and answer.is_integer():
        answer = int(answer)
    return {"submission": {"type": out.type, "answer": answer}}


builder = StateGraph(QueryState, context_schema=QueryContext)
builder.add_node(agent)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_node(finalize)
builder.add_edge(START, "agent")
builder.add_edge("tools", "agent")
builder.add_edge("finalize", END)
query_agent = builder.compile()
