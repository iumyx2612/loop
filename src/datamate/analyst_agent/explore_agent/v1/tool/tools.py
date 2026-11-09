"""The search subgraph's tools: peek, column_values, find_rows (LangGraph wrappers of explore.py).

README and the file list are not tools: the prepare node puts them in the state and the prompts show them.
InjectedState fills shop_dir and header_rows from the graph state; the LLM never sees or sets them.
A bad argument (unknown file, sheet, column) returns "ERROR: ..." with what exists, so the agent can retry.
"""
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.datamate.analyst_agent.explore_agent import explore

FILE = "File name exactly as listed, e.g. 'SoBanHang.xlsx'."
SHEET = ("Sheet name for xlsx, table name for sqlite, empty for csv. For column_values / find_rows, empty means "
         "every sheet of the file and every split file with the same header (e.g. X_1.xlsx and X_2026.xlsx).")


class _Injected(BaseModel):
    shop_dir: Annotated[str, InjectedState("shop_dir")]
    header_rows: Annotated[dict, InjectedState("header_rows")]


class PeekInput(_Injected):
    file: str = Field(description=FILE)
    sheet: str = Field(default="", description=SHEET)
    start: int = Field(default=1, description="First row to show (row 1 = first row of the sheet).")
    n: int = Field(default=10, description=f"Number of rows, at most {explore.MAX_PEEK_ROWS}.")


class ColumnValuesInput(_Injected):
    file: str = Field(description=FILE)
    sheet: str = Field(default="", description=SHEET)
    column: str = Field(description="Column name exactly as in the header.")


class FindRowsInput(_Injected):
    file: str = Field(description=FILE)
    sheet: str = Field(default="", description=SHEET)
    text: str = Field(description="Text to look for in any cell; case and accents are ignored.")


def _run(fn, *args, **kwargs) -> str:
    try:
        return fn(*args, **kwargs)
    except explore.ExploreError as e:
        return f"ERROR: {e}"


@tool("peek", args_schema=PeekInput)
def peek(file: str, sheet: str, start: int, n: int, shop_dir: str, header_rows: dict) -> str:
    """Show raw rows of one table, cells as stored (text in quotes, real dates as <datetime …>).
    Use it to see the layout: rows above the header, order fields filled only on an order's first line,
    footers at the end of a sheet (use the row count from the file list to start near the end)."""
    return _run(explore.peek, Path(shop_dir), file, sheet or None, start, n, header_rows)


@tool("column_values", args_schema=ColumnValuesInput)
def column_values(file: str, sheet: str, column: str, shop_dir: str, header_rows: dict) -> str:
    """Summarize one column over all its rows: every distinct value with its count when there are at most
    30 (statuses, units, channels, payment methods, groups), otherwise the formats with an example each
    (money, dates, quantities), plus min / max of numbers and dates."""
    return _run(explore.column_values, Path(shop_dir), file, sheet or None, column, header_rows)


@tool("find_rows", args_schema=FindRowsInput)
def find_rows(file: str, sheet: str, text: str, shop_dir: str, header_rows: dict) -> str:
    """Find the rows holding some text in any cell, with their row numbers and how many there are, e.g.
    subtotal rows ('Tổng ngày', 'Cộng'), service fees ('Phí phục vụ'), vouchers, shipping fees."""
    return _run(explore.find_rows, Path(shop_dir), file, sheet or None, text, header_rows)


TOOLS = [peek, column_values, find_rows]
