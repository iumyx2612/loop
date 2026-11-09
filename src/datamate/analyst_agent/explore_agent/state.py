"""Shared by every version of the explore_agent: the plan it returns, the runtime context, and the base state the
fixed steps (nodes.py) fill. Each version's own state extends ShopState (v1: v1/think/state.py).

The model is not in the state: it goes in the runtime context (ExploreContext), like the query_agent's
QueryContext. The state is what changes during a run and what the parent analyst_agent reads.

The plan is a Pydantic model because it is produced with `with_structured_output(SalesLinePlan,
method="json_schema")`: the model's fields become the JSON schema the LLM must fill, and the `description`
of each Field is shown to the LLM as part of that schema. Lists of small objects are used instead of dicts with
free keys (e.g. one StatusWord per raw word, not {"completed": [...]}): free-key dicts become
`additionalProperties` in JSON schema, which strict structured output and small models handle badly.

Tables are named the way the tools name them: `file` + `sheet` (xlsx sheet, or sqlite table; None for a csv, or
for every sheet of an xlsx whose sheets share one header, e.g. month sheets). For split files (`_1`, `_2`,
`_2025`) list every file in `line_tables`; elsewhere any one file of the group may stand for it.
"""
import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

Status = Literal["completed", "cancelled", "returned", "in_delivery"]


class TableRef(BaseModel):
    file: str
    sheet: str | None = Field(None, description="xlsx sheet or sqlite table; None = csv, or every sheet of the xlsx")


class ColumnSource(BaseModel):
    """One sales_lines column and the raw column it is read from."""
    schema_column: str = Field(description="a sales_lines column, e.g. order_date, or one of the working columns "
                                           "customer_code, line_product_name")
    file: str = Field(description="for a column of the line tables, any one of them: split files and month sheets "
                                  "with the same header are read together, so one entry per column is enough")
    sheet: str | None = None
    raw_column: str = Field(description="the column name exactly as in the file's header")


class Join(BaseModel):
    """Rows of `file` attach to rows of `on_file` where `key` (in `file`) equals `on_key` (in `on_file`).
    E.g. biz_108: orders `Mã hóa đơn` -> line file `Mã hóa đơn`; customers `Mã khách hàng` -> orders `Mã KH`."""
    file: str = Field(description="the table being looked up (orders, products, customers, ...)")
    sheet: str | None = None
    key: str = Field(description="its key column: one value = one row")
    on_file: str = Field(description="the table it attaches to: a line table, or a table joined earlier")
    on_sheet: str | None = None
    on_key: str = Field(description="the matching column in on_file")


class StatusWord(BaseModel):
    raw: str = Field(description="a status value exactly as written in the file")
    status: Status


class ExtraAttribute(BaseModel):
    """A product attribute the schema has no column for yet (brand, size, formula number, vehicle model, ...).
    Recorded so it is not lost; converted once the schema has generic attribute columns."""
    name: str = Field(description="short English name, e.g. brand")
    file: str
    sheet: str | None = None
    raw_column: str


class SalesLinePlan(BaseModel):
    """Where every sales_lines column of one shop comes from. Columns the converter computes itself (customer_key,
    product_key, line_kind, is_return_line, unit_factor, qty_base when only the conversions give it, an implied
    line_discount, source_*) are not mapped here."""
    line_tables: list[TableRef] = Field(description="the tables whose rows are sales lines (or orders, when the shop "
                                                    "records one line per order)")
    columns: list[ColumnSource] = Field(description="one entry per sales_lines column the shop records")
    missing_columns: list[str] = Field(description="sales_lines columns the shop does not record anywhere")
    joins: list[Join] = Field(description="how the other tables attach to the line tables; [] for a single table")
    forward_fill_columns: list[str] = Field(
        description="raw columns of the line table that are written only on an order's first line and blank on the "
                    "lines below it, order id first; [] when every line has them")
    statuses: list[StatusWord] = Field(description="every distinct raw status value; [] when there is no status")
    blank_status: Status | None = Field(
        None, description="the status of an order whose status cell is still blank after forward fill (e.g. a status "
                          "written in a notes column: a blank note = completed); None when that never happens")
    conversions: Literal["column", "file", "readme", "none"] = Field(
        description="where base-unit quantities come from: a column on the line (e.g. 'Số lượng quy đổi'), a "
                    "conversion file, the README's list, or none (every product sold in its base unit)")
    conversion_table: TableRef | None = Field(None, description="the conversion file, when conversions = file")
    return_lines: bool = Field(description="returns are written as negative-quantity lines of the sale's order")
    service_fee_names: list[str] = Field(description="product-name text of service-fee lines, e.g. 'Phí phục vụ'")
    non_revenue_names: list[str] = Field(
        description="product-name text of rows that are neither a product nor revenue (voucher, points, shipping)")
    subtotal_labels: list[str] = Field(description="text of subtotal / footer rows, e.g. 'Tổng ngày 01/03'")
    extra_attributes: list[ExtraAttribute]
    unmapped: list[str] = Field(description="'file / column: what it holds' for data that fits no sales_lines "
                                            "column (debt, stock, imports, ...)")
    notes: list[str] = Field(description="anything else the converter must know, e.g. toppings written in the "
                                         "drink's cell, dates without a year")


class ShopState(TypedDict):
    """The fields the fixed steps fill (confirm_headers, prepare in nodes.py). A version's state extends it:
    `class ExploreAgentState(ShopState): ...`; LangGraph sees one flat state with the fields of both."""
    shop_id: str  # e.g. "biz_117"
    shop_dir: str  # dataset/zaloAI/<split>/data/<shop>: the id alone does not say which split
    # Header rows decided before any exploring, for xlsx tables the README does not describe:
    # explore.table_key(file, sheet) -> 1-based row. Tools read it through InjectedState.
    header_rows: dict[str, int]
    readme: str  # README.md, set by prepare
    files: str  # explore.list_files: every table with its header row, columns and row count, set by prepare
    # One record per graph step (prompt, answer, problems, token usage), for the runner's trace file;
    # operator.add appends
    trace: Annotated[list[dict], operator.add]


@dataclass
class ExploreContext:
    """Fixed for the whole run and passed at invoke time; nodes read it via runtime.context."""

    model: BaseChatModel
    rulebook: str = ""  # the split's rulebook.md; the prompts hold §1, §3, §6, §8 (utils.rulebook_sections)
