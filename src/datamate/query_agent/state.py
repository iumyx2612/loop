"""Graph state of the query_agent (answers questions)."""
from typing import Annotated, TypedDict

import pandas as pd
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_results(old: dict | None, new: dict | None) -> dict:
    """Reducer for `results`: tools return only their new handle ({"r3": frame}); LangGraph merges it in.
    Without a reducer, two parallel information_finder calls would overwrite each other's update."""
    return {**(old or {}), **(new or {})}


class QueryState(TypedDict):
    question: str
    as_of_date: str  # "today" for the question, YYYY-MM-DD
    shop_dir: str  # data_std/<split>/<shop>: sales_lines.parquet + manifest.json
    calendar_path: str  # <split>/calendar.csv, for Tết periods
    messages: Annotated[list[AnyMessage], add_messages]
    results: Annotated[dict[str, pd.DataFrame], merge_results]  # handle -> full result frame, read by calculate
    submission: dict
