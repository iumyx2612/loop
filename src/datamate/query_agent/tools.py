"""The answering agent's tools: find_entity, information_finder, calculate.

LangGraph pieces used here:
  - InjectedState: tool arguments filled from the graph state (shop folder, as_of_date, stored results);
    they are left out of the schema the LLM sees, so it cannot set them.
  - InjectedToolCallId: the id of the tool call being answered, needed to build our own ToolMessage.
  - Command(update=...): information_finder returns a state update instead of a plain string, so it can
    write its full result frame into state["results"] next to the ToolMessage the LLM reads.
"""
import datetime as dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.datamate.query_agent import calc, entities, finder
from src.datamate.query_agent.periods import LunarCalendar
from src.datamate.query_agent.query import Shop


@lru_cache(maxsize=64)
def _shop(shop_dir: str) -> Shop:
    return Shop.load(Path(shop_dir))


@lru_cache(maxsize=4)
def _lunar(path: str) -> LunarCalendar:
    return LunarCalendar(Path(path))


# --- find_entity ---------------------------------------------------------------------------------

class FindEntityInput(BaseModel):
    kind: Literal["product", "product_group", "customer", "customer_group", "seller"] = Field(
        description="What the text names.")
    text: str = Field(description="The name as the question writes it, without surrounding words, "
                                  "e.g. 'Enterogermina 2 tỷ/5ml hộp 20 ống', 'cát đá', 'than thiet'.")
    shop_dir: Annotated[str, InjectedState("shop_dir")]


@tool("find_entity", args_schema=FindEntityInput)
def find_entity(kind: str, text: str, shop_dir: str) -> str:
    """Look up a product, product group, customer, customer group or seller named in the question and return
    the shop's closest values with their exact keys (score 1.0 = every word of the name is in the text).
    Use the key in information_finder filters (product_keys, customer_keys, product_group, customer_group, seller).
    Not needed for channel, payment method, shift or size: those have fixed values."""
    try:
        table = entities.candidates(_shop(shop_dir), kind, text)
    except LookupError as e:
        return f"NO DATA: {e}"
    return finder._table(table, entities.MAX_CANDIDATES)


# --- information_finder --------------------------------------------------------------------------

class Period(BaseModel):
    kind: Literal["day", "today", "yesterday", "this_week", "last_week", "this_month", "month", "quarter", "year",
                  "year_to_date", "last_n_days", "range", "all_before", "tet"] = Field(description=(
        "day (date) | today | yesterday | this_week (Monday → today) | last_week (Mon–Sun) | "
        "this_month (1st → today) | month (month, year; year omitted = latest one not after today) | "
        "quarter (quarter, year) | year (year) | year_to_date | last_n_days (n, includes today) | "
        "range (from_date, to_date, both included) | all_before (date: every day before it, from the start of data) | "
        "tet (year: 23 tháng Chạp → mùng 10 tháng Giêng)"))
    date: str | None = Field(None, description="YYYY-MM-DD, for day and all_before")
    month: int | None = None
    quarter: int | None = None
    year: int | None = None
    n: int | None = None
    from_date: str | None = Field(None, description="YYYY-MM-DD, for range")
    to_date: str | None = Field(None, description="YYYY-MM-DD, for range")

    def spec(self) -> dict:
        d = self.model_dump(exclude_none=True)
        if "from_date" in d:
            d["from"] = d.pop("from_date")
        if "to_date" in d:
            d["to"] = d.pop("to_date")
        return d


class Filters(BaseModel):
    product_keys: list[str] | None = Field(None, description="keys from find_entity(kind='product')")
    product_group: str | None = Field(None, description="exact group from find_entity(kind='product_group')")
    customer_keys: list[str] | None = Field(None, description="keys from find_entity(kind='customer')")
    customer_group: str | None = Field(None, description="exact group from find_entity(kind='customer_group')")
    channel: Literal["store", "zalo_oa", "phone", "ecommerce"] | None = Field(
        None, description="store = tại quầy / tại cửa hàng; ecommerce = sàn thương mại điện tử (sàn TMĐT)")
    payment_method: Literal["cash", "transfer", "ewallet", "cod", "debt"] | None = Field(
        None, description="as written on the order; cod is its own method, not cash; debt = ghi nợ")
    seller: str | None = Field(None, description="staff code from find_entity(kind='seller')")
    shift: Literal["morning", "afternoon"] | None = Field(None, description="ca sáng 06:00–13:59, ca chiều 14:00–21:59")
    size: Literal["M", "L"] | None = Field(None, description="drinks whose name ends with (M) / (L)")
    base_unit: str | None = Field(None, description="only products whose base unit is this, e.g. 'chai' for 'bán được mấy chai'")


class InformationFinderInput(BaseModel):
    measure: Literal["revenue", "order_count", "avg_order_value", "qty_base", "customer_count",
                     "cancelled_count", "cancel_rate", "unit_price_base", "gross_profit"]
    period: Period
    filters: Filters | None = None
    group_by: Literal["product", "product_group", "customer", "customer_group", "seller", "channel",
                      "payment_method", "order", "day", "week", "month", "shift"] | None = Field(
        None, description="one row per group, sorted by value desc (ties by code); omit for a single number. "
                          "order = one row per order (e.g. count orders with revenue ≥ X)")
    top_n: int | None = Field(None, description="how many rows to show you (all rows are kept for calculate); default 10")
    compare: Literal["previous"] | None = Field(None, description=(
        "also compute the period it is compared with (previous month / quarter / week / day, same days of last month "
        "for this_month, the N days before for last_n_days and range, the days before 23 tháng Chạp for tet) and "
        "add value_prev, diff (asked − compared) and pct_change"))
    state: Annotated[dict, InjectedState]
    tool_call_id: Annotated[str, InjectedToolCallId]


def _next_handle(state: dict, tool_call_id: str) -> str:
    """r1, r2, ... in call order. Parallel calls of one turn all see the same state, so a counter in the state
    would give them the same name; instead: earlier finder results + this call's position in the current turn."""
    messages = state.get("messages", [])
    earlier = sum(1 for m in messages if isinstance(m, ToolMessage) and m.name == "information_finder")
    turn = next(m for m in reversed(messages) if isinstance(m, AIMessage) and m.tool_calls)
    ids = [c["id"] for c in turn.tool_calls if c["name"] == "information_finder"]
    return f"r{earlier + ids.index(tool_call_id) + 1}"


@tool("information_finder", args_schema=InformationFinderInput)
def information_finder(measure: str, period: Period, state: dict, tool_call_id: str, filters: Filters | None = None,
                       group_by: str | None = None, top_n: int | None = None, compare: str | None = None) -> Command:
    """Compute one measure of the shop's sales over a period, optionally filtered, grouped and compared with the
    previous period. The full result is stored under a handle (r1, r2, ...) for calculate; you see a summary.

    The measure decides which orders count (rulebook), so do not filter by status yourself:
      revenue          Σ line amounts of orders not cancelled and not returned (service fee lines included)
      order_count      number of such orders
      avg_order_value  revenue ÷ order_count, rounded to the đồng
      qty_base         quantity in base units of such orders, product lines only (bán chạy, bán được mấy <đơn vị>)
      customer_count   distinct customers with such an order (walk-in excluded)
      cancelled_count  number of cancelled orders
      cancel_rate      cancelled ÷ all orders of any status × 100, 2 decimals
      unit_price_base  price per base unit before line discount, lines of every status (giá bán X ngày D)
      gross_profit     lãi gộp: revenue − Σ qty × the cost price written on each sale line, same orders as revenue
    Returns NO DATA when the shop does not record what is needed or the period is outside its data: then the
    question is unanswerable."""
    handle = _next_handle(state, tool_call_id)
    out = finder.information_finder(
        _shop(state["shop_dir"]), dt.date.fromisoformat(state["as_of_date"]), _lunar(state["calendar_path"]), handle,
        measure, period.spec(), filters.model_dump(exclude_none=True) if filters else None, group_by, top_n, compare)
    update = {"messages": [ToolMessage(out.text, tool_call_id=tool_call_id, name="information_finder")]}
    if out.handle:
        update["results"] = {out.handle: out.frame}
    return Command(update=update)


# --- calculate -----------------------------------------------------------------------------------

class CalculateInput(BaseModel):
    expression: str = Field(description=(
        "e.g. r1 / r2 * 100, share(r1, r2), r1.pct_change, first(r1.name), top(r1.name, 5), count(r1.value >= 3), "
        "count(setdiff(r1.key, r2.key)), pct_change(value(r1, 'afternoon'), value(r1, 'morning')), increasing(r1)"))
    results: Annotated[dict, InjectedState("results")]


@tool("calculate", args_schema=CalculateInput)
def calculate(expression: str, results: dict) -> str:
    """Evaluate an expression over numbers and result handles. A single-value handle (r1) reads as its number,
    its columns as r1.value_prev, r1.pct_change...; a grouped handle is a table whose columns are r1.key, r1.code,
    r1.name, r1.value (all rows, not only the ones shown to you).
    Functions: count total mean first top value where setdiff intersect pct_change share increasing decreasing
    round min max abs. pct_change and share give percent with 2 decimals; round(x, 2) rounds half up."""
    try:
        return json.dumps(calc.calculate(expression, results or {}), ensure_ascii=False)
    except calc.CalcError as e:
        return f"ERROR: {e}"


TOOLS = [find_entity, information_finder, calculate]
