"""Nodes of the search subgraph. The other node, "tools", is LangGraph's prebuilt ToolNode (see agent.py)."""
from typing import Literal

from langchain_core.messages import SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command

from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.v1.tool.prompts import SEARCH_SYSTEM
from src.datamate.analyst_agent.explore_agent.v1.tool.state import SearchState
from src.datamate.analyst_agent.explore_agent.v1.tool.tools import TOOLS


def call_llm(state: SearchState, runtime: Runtime[ExploreContext]) -> Command[Literal["tools", "__end__"]]:
    """Let the LLM pick tools; run them if it asked for any, otherwise finish."""
    llm = runtime.context.model.bind_tools(TOOLS)
    response = llm.invoke([SystemMessage(SEARCH_SYSTEM), *state["messages"]])
    return Command(update={"messages": [response]}, goto="tools" if response.tool_calls else END)
