"""The search subgraph: one request from think -> a ReAct loop over peek / column_values / find_rows -> a report.

    START → llm ⇄ tools → END        (llm stops when it replies without tool calls)

Like the qna tool_agent: every request starts with a fresh message list (the request and the file list only),
so earlier rounds never fill its context; think keeps what matters as notes.

What think's `search` node uses from here: run_search (run one request), then format_report (the report think
reads) and transcript (the full run, for the trace).
"""
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, StateGraph
from langgraph.prebuilt import ToolNode

from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.v1.tool.nodes import call_llm
from src.datamate.analyst_agent.explore_agent.v1.tool.prompts import SEARCH_USER
from src.datamate.analyst_agent.explore_agent.v1.tool.state import SearchState
from src.datamate.analyst_agent.explore_agent.v1.tool.tools import TOOLS

# llm ⇄ tools is 2 steps per tool turn: about 6 tool turns per request, then the run is cut and the report is
# built from what was gathered so far
RECURSION_LIMIT = 13
NOTHING_USABLE = "(the search agent found nothing usable this round)"


def build_graph():
    g = StateGraph(SearchState, context_schema=ExploreContext)
    g.add_node("llm", call_llm)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "llm")
    g.add_edge("tools", "llm")
    return g.compile()


search_graph = build_graph()


def _text(m: AIMessage) -> str:
    if isinstance(m.content, str):
        return m.content.strip()
    return " ".join(b.get("text", "") for b in m.content if isinstance(b, dict)).strip()


def format_report(messages: list[AnyMessage]) -> str:
    """Each tool call with its exact output, then the search agent's closing sentence. Calls that failed (bad
    file / column names: the agent already retried with the right ones) and repeated calls are left out."""
    args_by_id = {c["id"]: c["args"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    entries = []
    for m in messages:
        if not isinstance(m, ToolMessage) or str(m.content).startswith("ERROR"):
            continue
        args = ", ".join(f"{k}={v!r}" for k, v in args_by_id.get(m.tool_call_id, {}).items())
        entry = f"[{m.name}({args})]\n{m.content}"
        if entry not in entries:
            entries.append(entry)
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and not last.tool_calls and _text(last):
        entries.append(f"[search agent's summary] {_text(last)}")
    return "\n\n".join(entries) or NOTHING_USABLE


def transcript(messages: list[AnyMessage]) -> list[dict]:
    """The search run step by step for the trace: each LLM turn (its text and tool calls) and each tool output in
    full, ERROR outputs included (the report leaves them out). The request itself is in the trace record."""
    out = []
    for m in messages:
        if isinstance(m, AIMessage):
            out.append({"llm": _text(m), "tool_calls": [{"name": c["name"], "args": c["args"], "id": c["id"]}
                                                        for c in m.tool_calls]})
        elif isinstance(m, ToolMessage):
            out.append({"tool": m.name, "id": m.tool_call_id, "output": str(m.content)})
    return out


def run_search(request: str, files: str, shop_dir: str, header_rows: dict, context) -> tuple[list[AnyMessage], bool]:
    """Run one request; returns the messages reached and whether the recursion limit cut the run.
    stream(stream_mode="values") yields the whole state after every step, so the last one is kept."""
    state = {"messages": [HumanMessage(SEARCH_USER.format(files=files, request=request))],
             "shop_dir": shop_dir, "header_rows": header_rows}
    messages, cut = state["messages"], False
    try:
        for values in search_graph.stream(state, context=context, stream_mode="values",
                                          config={"recursion_limit": RECURSION_LIMIT}):
            messages = values["messages"]
    except GraphRecursionError:
        cut = True
    return messages, cut
