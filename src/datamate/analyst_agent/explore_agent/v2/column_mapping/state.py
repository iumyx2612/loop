"""The column-mapping subgraph's own state (agent.py). The parent graph never sees it: map_line_columns (agent.py)
builds the input from the parent state, runs the subgraph, and returns only the parent's fields."""
import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage

from src.datamate.analyst_agent.explore_agent.state import ColumnSource, TableRef


class ColumnMappingState(TypedDict):
    # inputs, from the parent state
    shop_dir: str
    header_rows: dict[str, int]
    skipped_rows: dict[str, list[int]]  # the filter step's rows (step 0): left out of every check, grid and profile
    line_tables: list[TableRef]
    raw_columns: list[str]  # the line table's column names: the enum of every reply field
    # the messages of the next call, replaced by each check (no reducer: a new value overwrites the old one): the
    # first message; after problems, the one self-contained retry message (no history, user 2026-11-07); after an
    # unreadable reply, the same request + that reply + what was wrong with it
    request: list[AnyMessage]
    last_reply: str  # the last reply as text (JSON when it was read), for the unreadable-reply request
    fields: list[str]  # the sales_lines columns the next reply answers (all at first, fewer on a retry)
    answer: dict[str, str]  # sales_lines column -> raw column or NOT_HERE, merged over the replies
    reply_error: str  # the last reply could not be read: why ("" = it was read)
    attempts: int  # replies so far
    # the last check's result (check node)
    columns: list[ColumnSource]
    open_columns: list[str]
    problems: list[str]
    checks: dict  # each check's record
    # one record per node run, for the parent's trace record (operator.add appends)
    log: Annotated[list[dict], operator.add]
