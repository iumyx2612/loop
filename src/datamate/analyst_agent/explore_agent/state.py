"""Shared by the explore_agent: the plan it returns, the runtime context, and the base state the fixed steps
(nodes.py) fill. The workflow's state (v2/state.py WorkflowState) extends ShopState.

The model is not in the state: it goes in the runtime context (ExploreContext), like the query_agent's
QueryContext. The state is what changes during a run and what the parent analyst_agent reads.

The plan is plain data: v2's write_plan (step 8) builds it in code, and no LLM ever fills it (every v2 LLM call has
its own small enum schema). It was an LLM reply schema in the removed v1, which is why it uses lists of small objects
(one StatusWord per raw word) and the Field descriptions read as instructions.

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
    alt_key: str | None = Field(
        None, description="a second key column of `file`: a row attaches when on_key equals key OR alt_key. For a "
                          "line column holding a code on some lines and the name on others (dev_011 / dev_014: 'Hàng' "
                          "= 'Ghi tắt' or 'Tên hàng'); match accent-insensitively. None for a one-column join")


class StatusWord(BaseModel):
    raw: str = Field(description="a status value exactly as written in the file")
    status: Status


class ConversionRow(BaseModel):
    """One big unit of one product, read out of README prose by the explore step (kind = readme_text)."""
    product: str = Field(description="the product as the README writes it")
    unit: str = Field(description="the big unit, e.g. bó")
    factor: float = Field(description="base units in one big unit, e.g. 10")
    base_unit: str | None = Field(None, description="the base unit when the text says it, e.g. cây")


class ConversionSource(BaseModel):
    """One place where the shop writes its unit conversions (design agreed 2026-11-12; survey/conversions.md).
    kind:
    - readme_list: the README's list '  - <product>: 1 <big unit> = <n> <base unit>' (common.readme_conversions);
      no other field.
    - table: a table (xlsx sheet / csv / sqlite table) with one row per product × big unit, or a catalog with the
      conversions in a text column; the columns below say which column holds what.
    - readme_text: conversions written in README prose (dev_004 'Hàng theo bó'); `text` quotes the line, `rows` are
      what the explore step read out of it.
    The converter reads every source (a shop may have several, dev_004: a table + prose)."""
    kind: Literal["readme_list", "table", "readme_text"]
    table: TableRef | None = None
    key: Literal["code", "name"] | None = Field(
        None, description="table: the product column holds the product code or the product name")
    product_column: str | None = None
    unit_column: str | None = Field(None, description="table: the big unit; None when factor_format = text (the "
                                                      "unit is inside the factor cell)")
    factor_column: str | None = None
    factor_format: Literal["number", "nested", "text"] | None = Field(
        None, description="table: number = base units in one big unit (24); nested = a number in base units or "
                          "'<n> <unit>' in the product's unit just below (dev_005: '15 vỉ'); text = '1 <big unit> = "
                          "<n> <base unit>' parts joined by ';' (dev_013: '1 thùng = 4 bịch; 1 lốc = 4 gói'), blank "
                          "= base unit only")
    base_unit_column: str | None = Field(None, description="table: the product's base unit, when the table has it")
    text: str | None = Field(None, description="readme_text: the README line(s) the rows were read from")
    rows: list[ConversionRow] = Field(default_factory=list, description="readme_text: the conversions in the text")


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
    line_discount, source_*) are not mapped here. unit_factor: qty_base / qty when qty_base is mapped, else 1 on a
    line in the base unit and the conversions' factor for (product, unit) on a big-unit line."""
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
    conversions: list[ConversionSource] = Field(
        default_factory=list,
        description="every place the shop writes its unit conversions (big unit -> base units); [] when it has none. "
                    "A base-unit quantity column on the line is not listed here: it is the mapped qty_base column")
    return_lines: bool = Field(description="returns are written as negative-quantity lines of the sale's order")
    service_fee_names: list[str] = Field(description="product-name text of service-fee lines, e.g. 'Phí phục vụ'")
    non_revenue_names: list[str] = Field(
        description="product-name text of rows that are neither a product nor revenue (voucher, points, shipping)")
    subtotal_labels: list[str] = Field(
        description="regexes of the line tables' subtotal / footer / note rows (from the filter step), matched "
                    "case-insensitively against the row's word cells joined by ' | ' (filter_rows.rows.row_text)")
    skipped_rows: dict[str, list[int]] = Field(
        default_factory=dict,
        description="rows that are not data (repeated headers, subtotal / footer / note rows, also label-less ones no "
                    "regex covers), explore.table_key(file, sheet) -> row numbers as in the file; the converter "
                    "drops them")
    header_rows: dict[str, int] = Field(
        default_factory=dict,
        description="header rows decided by confirm_headers, explore.table_key(file, sheet) -> 1-based row; tables "
                    "not listed take the README's header row, else the guess (explore._table), so reading the plan's "
                    "tables with explore.tables(..., header_rows, skipped_rows) gives the rows the explore steps saw")
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
