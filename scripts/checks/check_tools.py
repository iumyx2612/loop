"""The LangGraph tools without an LLM: hand-written AIMessages with tool calls go through a real ToolNode.
Checks InjectedState, Command updates, the results reducer and handle naming of parallel calls.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_tools.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.prebuilt import ToolNode  # noqa: E402

from src.datamate.query_agent.state import QueryState  # noqa: E402
from src.datamate.query_agent.tools import TOOLS  # noqa: E402

builder = StateGraph(QueryState)
builder.add_node("tools", ToolNode(TOOLS))
builder.add_edge(START, "tools")
builder.add_edge("tools", END)
graph = builder.compile()


def call(name, args, i):
    return {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}


def turn(state, *calls):
    """Append one AI turn with these tool calls, run the ToolNode, print what the LLM would read."""
    state = {**state, "messages": [*state["messages"], AIMessage("", tool_calls=list(calls))]}
    state = graph.invoke(state)
    for m in state["messages"][-len(calls):]:
        print(f"--- {m.name} ({m.tool_call_id})\n{m.content}")
    return state


failures = []


def expect(what, got, want):
    if got != want:
        failures.append(f"{what}: got {got!r}, want {want!r}")


# What the LLM sees of each tool: injected arguments must not be there
for t in TOOLS:
    props = t.tool_call_schema.model_json_schema().get("properties", {})
    print(f"{t.name}: LLM-visible args {sorted(props)}")
    expect(f"{t.name} hides injected args", bool({"state", "tool_call_id", "results", "shop_dir"} & set(props)), False)

state = {"question": "Cho mình hỏi tháng 3/2026 nhóm cát đá chiếm bao nhiêu % doanh thu?", "as_of_date": "2026-09-03",
         "shop_dir": str(ROOT / "data_std/train/biz_116"), "calendar_path": str(ROOT / "dataset/zaloAI/train/calendar.csv"),
         "messages": [HumanMessage("…")], "results": {}}

# Turn 1: find the group
state = turn(state, call("find_entity", {"kind": "product_group", "text": "cát đá"}, 1))
# Turn 2: two parallel finder calls → must become r1 and r2, both stored
march = {"kind": "month", "month": 3, "year": 2026}
state = turn(state, call("information_finder", {"measure": "revenue", "period": march, "filters": {"product_group": "cát đá"}}, 2),
             call("information_finder", {"measure": "revenue", "period": march}, 3))
expect("handles after turn 2", sorted(state["results"]), ["r1", "r2"])
# Turn 3: calculate over both handles (key: 13.74)
state = turn(state, call("calculate", {"expression": "share(r1, r2)"}, 4))
expect("share", json.loads(state["messages"][-1].content), 13.74)
# Turn 4: a NO DATA call (116 has no seller) does not store a handle; the next call is still numbered r4
state = turn(state, call("information_finder", {"measure": "revenue", "period": march, "group_by": "seller"}, 5),
             call("information_finder", {"measure": "order_count", "period": march, "compare": "previous"}, 6))
expect("handles after turn 4", sorted(state["results"]), ["r1", "r2", "r4"])
expect("no data text", state["messages"][-2].content.startswith("NO DATA"), True)
# Turn 5: errors come back as text the LLM can fix
state = turn(state, call("calculate", {"expression": "r3 + 1"}, 7), call("calculate", {"expression": "__import__('os')"}, 8),
             call("information_finder", {"measure": "revenue", "period": {"kind": "month", "month": 13}}, 9))
expect("unknown handle", state["messages"][-3].content.startswith("ERROR: unknown name 'r3'"), True)
expect("blocked call", state["messages"][-2].content.startswith("ERROR"), True)
expect("bad period", state["messages"][-1].content.startswith("ERROR"), True)

print("\n" + ("\n".join(failures) if failures else "all checks ok"))
