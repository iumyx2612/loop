from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class SearchState(TypedDict):
    """The search subgraph: a fresh ReAct loop over the three tools for one request. shop_dir and header_rows
    are copied in from the parent because the tools read them through InjectedState."""
    messages: Annotated[list[AnyMessage], add_messages]
    shop_dir: str
    header_rows: dict[str, int]
