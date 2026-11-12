"""The conversions step, map_conversions: every place the shop writes its unit conversions (big unit -> base units),
written into state["conversions"] (the plan's conversions, a list of state.ConversionSource). Design in
explore_agent.md, "Conversions"; survey in survey/conversions.md. Parts agreed with the user on 2026-11-12:

Part 1, the README.
- Code: common.readme_conversions finds a list '  - <product>: 1 <big unit> = <n> <base unit>' -> one source
  kind = readme_list, no LLM (it reads all 22 list shops, nothing in the other 22).
- Fallback (user's idea), when it finds none: the README's last section (user: the conversions are in the README's
  last part), asked one line at a time (the user's multi-step decompose: the whole section in one call missed
  dev_004's "Hàng theo bó" line next to a line naming DanhMuc.xlsx): per line a one-sentence reason first, then
  yes / no: does this line itself write a conversion with its number? The yes lines go to part 4. A shop may have
  both: dev_004's pipes are only in the README, its other products only in DanhMuc.xlsx.
Part 2, which table, only when the regex found no README list (no surveyed shop has both): one call over every table
group that is not the line tables (attached or not: dev_013's catalog holds its conversions), each with its row
count, columns, the README's meanings of them and 3 data rows, plus the README's last section; reason first, then
one enum of the option names + "none".
Part 3, the chosen table's columns: one call, reason first, then four enums of the table's columns: product, unit
(or IN_CELL), factor, base unit (or NOT_IN_TABLE). Code decides the rest from the values: factor_format = the first
of number / nested / text that reads ≥ 90 % of the factor cells (plan_check.factor_format_of; text -> no unit
column); key = code or name, whichever of the line's product values (step 2's columns, the snapshot's fills,
attach's product links) covers more of the table's products, at least MIN_PRODUCT_MATCH. Any problem: no source
(no retry yet), the problems in the trace.
Part 4, README text into rows, on the lines part 1 answered yes for: one call, reason first, then a list of rows
(product, unit, factor, base unit; at most MAX_ROWS). Code takes `text` = the yes lines naming the rows' products
(never quoted by the model) and checks: ≥ 1 row, factors > 0, every product in those lines, ≥ MIN_PRODUCT_MATCH of
the products among the line's product names. Any problem: no source.
"""
import re
import time
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.plan_check import factor_format_of
from src.datamate.analyst_agent.explore_agent.state import ConversionRow, ConversionSource, ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import label, since
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import (readme_meanings_text, reads_as_numbers,
                                                                              table_text)
from src.datamate.analyst_agent.explore_agent.v2.planner.conversions.prompts import (IN_CELL, NONE, NOT_IN_TABLE,
                                                                             README_ROWS, README_ROWS_REASON,
                                                                             README_LINE, README_WRITES_REASON,
                                                                             TABLE_CHOICE, TABLE_CHOICE_REASON,
                                                                             TABLE_COLUMNS, TABLE_COLUMNS_REASON)
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.attach import value_key
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.nodes import named_groups
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured
from src.datamate.analyst_agent.explore_agent.v2.planner.write_plan.nodes import table_refs
from src.datamate.parse import fold_accents, is_blank
from src.datamate.reference.common import readme_conversions

HEADER = re.compile(r"#{1,6}\s")
SAMPLE_ROWS = 3  # data rows shown per option in part 2, as step 1
GRID_ROWS = 6  # data rows shown in part 3: the fullest rows (a catalog's conversion cell is blank on many rows)
MIN_PRODUCT_MATCH = 0.5  # share of the table's products that must be found among the line's products
PRODUCT_KIND = {"product_code": "code", "product_name": "name", "line_product_name": "name"}
MAX_ROWS = 30  # part 4's rows list is capped (JSON-schema maxItems): an open-length list was padded by the 27B


# --- shared ---

def ask(runtime: Runtime[ExploreContext], schema: type[BaseModel], prompt: str) -> tuple[dict, dict]:
    """One call: (the reply as a dict, {} when unreadable or failed; the trace record of the call)."""
    rec = {"prompt": prompt}
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
        rec.update(call)
    except Exception as e:  # noqa: BLE001 - recorded; nothing is added
        rec["call_failed"] = f"{type(e).__name__}: {str(e)[:500]}"
        return {}, rec
    return (parsed.model_dump() if parsed is not None else {}), rec


def add_usage(total: dict, call: dict) -> dict:
    for k, v in (call.get("usage") or {}).items():
        total[k] = total.get(k, 0) + v
    return total


def group_cells(g: explore.TableGroup, column: str) -> list:
    """Every data cell of `column` over the group's tables (the filter step's rows left out)."""
    out = []
    for t in g.tables:
        i = explore.column_index(t, column)
        if i is not None:
            out += [r[i] if i < len(r) else None for r in t.data_rows()]
    return out


# --- part 1: the README ---

def last_section(readme: str) -> str:
    """The README from its last header line (any level) to the end; the whole README when it has no header."""
    lines = readme.strip().splitlines()
    starts = [i for i, line in enumerate(lines) if HEADER.match(line)]
    return "\n".join(lines[starts[-1]:] if starts else lines).strip()


def section_lines(section: str) -> list[str]:
    """The section's items, one per question: its lines below the header, an indented line (a sub-item or a wrapped
    line) joined to the item above it; blank lines dropped."""
    items: list[str] = []
    for line in section.splitlines():
        if not line.strip() or HEADER.match(line):
            continue
        if line[:1] in (" ", "\t") and items:
            items[-1] += "\n" + line
        else:
            items.append(line)
    return items


def writes_schema() -> type[BaseModel]:
    """The fallback call (one per line): reason (one sentence, first), then writes."""
    return create_model("ReadmeWrites",
                        reason=(str, Field(description=README_WRITES_REASON)),
                        writes=(Literal["yes", "no"], Field(description="the line itself writes a conversion with "
                                                                        "its number")))


def readme_sources(shop_dir: Path, readme: str, runtime: Runtime[ExploreContext]) -> tuple[list[ConversionSource], dict]:
    """Part 1: the README list by code, else the fallback question on each line of the last section.
    (sources, trace details; readme_writes["yes_lines"] go to part 4)."""
    rows = readme_conversions(shop_dir / "README.md")
    if not rows.empty:
        return [ConversionSource(kind="readme_list")], {"readme_list": len(rows)}
    asked, usage = [], {}
    for line in section_lines(last_section(readme)):
        reply, call = ask(runtime, writes_schema(), README_LINE.format(line=line))
        add_usage(usage, call)
        asked.append({**call, "line": line, "reason": reply.get("reason"), "writes": reply.get("writes")})
    info = {"readme_list": 0,
            "readme_writes": {"lines": asked, "yes_lines": [a["line"] for a in asked if a["writes"] == "yes"],
                              "usage": usage}}
    return [], info


# --- part 2: which table ---

def table_option_text(shop_dir: Path, g: explore.TableGroup, name: str) -> str:
    """One option: its tables and row count, columns, the README's meanings of them, the first data rows."""
    t = g.first
    rows = sum(len(m.data_numbers()) for m in g.tables)
    labels = [label(m.file, m.sheet) for m in g.tables]
    listed = ", ".join(labels) if len(labels) <= 3 else f"{labels[0]} … {labels[-1]}"
    meanings = readme_meanings_text(shop_dir, t).replace("\n", "\n    ")
    lines = [f"Option: {name}",
             f"  {len(labels)} table(s), {rows:,} rows below the header: {listed}",
             f"  columns: {' | '.join(t.columns)}",
             f"  README meanings of the columns:\n    {meanings}",
             f"  first rows of {labels[0]} (text cells in quotes):"]
    lines += ["  " + explore._row_line(i, t.rows[i - 1]) for i in t.data_numbers()[:SAMPLE_ROWS]]
    return "\n".join(lines)


def table_schema(names: list[str]) -> type[BaseModel]:
    """Part 2's reply: reason (one sentence, first), then option = one of `names` or NONE."""
    return create_model("ConversionTable",
                        reason=(str, Field(description=TABLE_CHOICE_REASON)),
                        option=(Literal[tuple([*names, NONE])],
                                Field(description='the option\'s name exactly as written after "Option:", or '
                                                  f'"{NONE}"')))


def choose_table(shop_dir: Path, options: list[tuple[str, explore.TableGroup]], section: str,
                 runtime: Runtime[ExploreContext]) -> tuple[str | None, dict]:
    """Part 2: (the chosen option's name or None, trace details)."""
    if not options:
        return None, {"options": [], "skipped": "no table besides the line tables"}
    names = [n for n, _ in options]
    prompt = TABLE_CHOICE.format(section=section, none=NONE,
                                 options="\n\n".join(table_option_text(shop_dir, g, n) for n, g in options))
    reply, call = ask(runtime, table_schema(names), prompt)
    option = reply.get("option")
    info = {"options": names, **call, "reason": reply.get("reason"), "option": option}
    return (option if option and option != NONE else None), info


# --- part 3: the chosen table's columns ---

def grid_rows(t: explore.Table, n: int = GRID_ROWS) -> list[int]:
    """The n fullest data rows (most filled cells; first ones on a tie), in file order."""
    numbers = t.data_numbers()
    filled = {i: sum(not is_blank(v) for v in t.rows[i - 1]) for i in numbers}
    return sorted(sorted(numbers, key=lambda i: (-filled[i], i))[:n])


def columns_schema(columns: list[str]) -> type[BaseModel]:
    """Part 3's reply: reason first, then the four columns, each an enum of the table's columns (+ IN_CELL for the
    unit, NOT_IN_TABLE for the base unit)."""
    cols = tuple(dict.fromkeys(columns))
    return create_model("ConversionColumns",
                        reason=(str, Field(description=TABLE_COLUMNS_REASON)),
                        product_column=(Literal[cols], Field(description="the product (code or name)")),
                        unit_column=(Literal[(*cols, IN_CELL)], Field(description="the big unit")),
                        factor_column=(Literal[cols], Field(description="base units in one big unit")),
                        base_unit_column=(Literal[(*cols, NOT_IN_TABLE)], Field(description="the base unit")))


def line_product_keys(state: WorkflowState, named: list[tuple[str, explore.TableGroup, bool]]) -> dict[str, dict]:
    """The line's products as value keys, by kind ('code' / 'name'), with where each came from: step 2's product
    columns on the line tables, the snapshot's fills of attached tables, attach's product links (both sides)."""
    groups = {n: g for n, g, _ in named}
    line = next((g for _, g, is_line in named if is_line), None)
    where: dict[str, set[tuple[str, str]]] = {"code": set(), "name": set()}
    line_name = next((n for n, _, is_line in named if is_line), None)
    for c in state.get("columns") or []:
        if c.schema_column in PRODUCT_KIND and line is not None:
            where[PRODUCT_KIND[c.schema_column]].add((line_name, c.raw_column))
    attached = {a["table"] for a in state.get("attached") or []}
    for s in state.get("snapshots") or []:
        if s["table"] in attached:
            for field, col in (s.get("filled") or {}).items():
                if field in PRODUCT_KIND:
                    where[PRODUCT_KIND[field]].add((s["table"], col))
    for a in state.get("attached") or []:
        for k in a.get("links") or []:
            if k["anchor"] in PRODUCT_KIND:
                where[PRODUCT_KIND[k["anchor"]]] |= {(a["table"], k["column"]), (k["to_table"], k["to_column"])}
    out = {}
    for kind, places in where.items():
        keys = {key for n, col in places if n in groups for v in group_cells(groups[n], col)
                if (key := value_key(v)) is not None}
        out[kind] = {"keys": keys, "from": sorted(f"{n} / {col}" for n, col in places)}
    return out


def map_table(state: WorkflowState, named: list[tuple[str, explore.TableGroup, bool]], name: str,
              runtime: Runtime[ExploreContext]) -> tuple[ConversionSource | None, dict]:
    """Part 3: (the table source, or None when a check failed; trace details)."""
    shop_dir = Path(state["shop_dir"])
    g = dict((n, grp) for n, grp, _ in named)[name]
    t = g.first
    rows = sum(len(m.data_numbers()) for m in g.tables)
    prompt = TABLE_COLUMNS.format(name=name, rows=f"{rows:,}", meanings=readme_meanings_text(shop_dir, t),
                                  table=table_text(t, rows=grid_rows(t)), in_cell=IN_CELL, not_here=NOT_IN_TABLE)
    reply, call = ask(runtime, columns_schema(t.columns), prompt)
    info = {**call, **{k: reply.get(k) for k in ("reason", "product_column", "unit_column", "factor_column",
                                                  "base_unit_column")}}
    if not reply:
        info["problems"] = ["no readable answer"]
        return None, info
    product, unit, factor, base = (reply["product_column"], reply["unit_column"], reply["factor_column"],
                                   reply["base_unit_column"])
    problems = []
    fmt = factor_format_of(group_cells(g, factor))
    info["factor_format"] = fmt
    if fmt is None:
        problems.append(f"factor_column {factor!r} reads neither as numbers, as '<n> <unit>' nor as "
                        "'1 <big unit> = <n> <base unit>'")
    if fmt == "text":
        unit = None  # the big unit is inside the factor cell
    elif unit == IN_CELL:
        problems.append(f"unit_column: answered inside the conversion cell, but {factor!r} reads as {fmt}")
    if len({product, factor} | ({unit} if unit else set())) < (3 if unit else 2):
        problems.append("product, unit and factor columns must be different columns")
    # base unit (agreed 2026-11-12): dropped, not failed, when it sits on a column another role holds (the unit as
    # answered, also IN_CELL's text column) or its cells read as numbers. The 9B named a column on all 6 code-keyed
    # tables, which have none (the big unit, the factor, biz_106's price 'Giá bán'), often while its reason said
    # the base unit is not present. The field is a fallback (base_unit normally comes from the products table)
    base = None if base == NOT_IN_TABLE else base
    if base is not None and (base in {product, factor, reply["unit_column"]} or reads_as_numbers(base, g.tables)):
        info["base_unit_dropped"] = base
        base = None
    # key: the kind of line product values that covers more of the table's products
    table_keys = {k for v in group_cells(g, product) if (k := value_key(v)) is not None}
    line_keys = line_product_keys(state, named)
    cover = {kind: (len(table_keys & v["keys"]) / len(table_keys) if table_keys else 0.0)
             for kind, v in line_keys.items()}
    info["key_match"] = {kind: {"coverage": round(cover[kind], 2), "from": line_keys[kind]["from"]}
                         for kind in cover}
    key = max(cover, key=cover.get) if cover else None
    if not any(v["keys"] for v in line_keys.values()):
        problems.append("no product code or name on the lines to compare the table's products with")
    elif cover[key] < MIN_PRODUCT_MATCH:
        problems.append(f"only {cover[key]:.0%} of the table's products ({product!r}) are among the line's "
                        f"products; the table does not describe the products sold")
    info["key"] = key
    refs = table_refs(shop_dir, g)
    if len(refs) > 1:
        info["warning"] = f"{name}: spread over {len(refs)} tables, named by the first"
    info["problems"] = problems
    if problems:
        return None, info
    return ConversionSource(kind="table", table=refs[0], key=key, product_column=product, unit_column=unit,
                            factor_column=factor, factor_format=fmt, base_unit_column=base), info


# --- part 4: README text into rows ---

class _Row(BaseModel):
    product: str = Field(description="the product's name exactly as the text writes it")
    unit: str = Field(description="the big unit")
    factor: float = Field(description="base units in one big unit")
    base_unit: str | None = Field(description="the base unit, or null when the text does not say")


def rows_schema() -> type[BaseModel]:
    """Part 4's reply: reason (one sentence, first), then at most MAX_ROWS rows."""
    return create_model("ReadmeRows",
                        reason=(str, Field(description=README_ROWS_REASON)),
                        rows=(list[_Row], Field(max_length=MAX_ROWS, description="one row per product and big unit")))


def readme_rows(state: WorkflowState, named: list[tuple[str, explore.TableGroup, bool]], yes_lines: list[str],
                runtime: Runtime[ExploreContext]) -> tuple[ConversionSource | None, dict]:
    """Part 4: the lines part 1 answered yes for, read into rows. (the readme_text source, or None when a check
    failed; trace details)."""
    section = "\n".join(yes_lines)
    reply, call = ask(runtime, rows_schema(), README_ROWS.format(lines=section))
    rows = [ConversionRow(**r) for r in reply.get("rows") or []]
    info = {**call, "reason": reply.get("reason"), "rows": [r.model_dump() for r in rows]}
    if not reply:
        info["problems"] = ["no readable answer"]
        return None, info
    problems = []
    if not rows:
        problems.append("no rows")
    folded = fold_accents(section) or ""
    for r in rows:
        if r.factor <= 0:
            problems.append(f"factor {r.factor} for {r.product!r} {r.unit!r} is not positive")
        if (fold_accents(r.product) or "") not in folded:
            problems.append(f"product {r.product!r} is not written in the text")
    # text: the yes lines that name a row's product (code, never quoted by the model)
    lines = [line for line in section.splitlines()
             if any((fold_accents(r.product) or "#") in (fold_accents(line) or "") for r in rows)]
    text = "\n".join(lines) or None
    names = line_product_keys(state, named)["name"]["keys"]
    found = [r.product for r in rows if value_key(r.product) in names]
    info["products_on_lines"] = f"{len(found)} of {len(rows)}"
    if rows and not names:
        problems.append("no product names on the lines to compare the text's products with")
    elif rows and len(found) / len(rows) < MIN_PRODUCT_MATCH:
        problems.append(f"only {len(found)} of the {len(rows)} products are among the line's product names")
    info["text"], info["problems"] = text, problems
    if problems:
        return None, info
    return ConversionSource(kind="readme_text", text=text, rows=rows), info


# --- the node ---

def map_conversions(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node."""
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    readme = state.get("readme") or (shop_dir / "README.md").read_text(encoding="utf-8")
    sources, info = readme_sources(shop_dir, readme, runtime)
    record = {"step": "map_conversions", **info}
    usage = add_usage({}, info.get("readme_writes") or {})
    if not info.get("readme_list"):  # parts 2-4 only when the regex found no README list
        named = named_groups(shop_dir, state.get("header_rows"), state.get("line_tables") or [],
                             state.get("skipped_rows"))
        section = last_section(readme)
        yes_lines = (info.get("readme_writes") or {}).get("yes_lines") or []
        if yes_lines:  # part 4
            source, rows = readme_rows(state, named, yes_lines, runtime)
            record["readme_rows"] = rows
            add_usage(usage, rows)
            if source is not None:
                sources.append(source)
        options = [(n, g) for n, g, is_line in named if not is_line]
        chosen, choice = choose_table(shop_dir, options, section, runtime)
        record["table_choice"] = choice
        add_usage(usage, choice)
        if chosen is not None:
            source, columns = map_table(state, named, chosen, runtime)
            record["table_columns"] = columns
            add_usage(usage, columns)
            if source is not None:
                sources.append(source)
    record["sources"] = [s.kind for s in sources]
    if usage:
        record["usage"] = usage  # every call of the step (the runner sums each record's usage)
    record["seconds"] = since(start)
    return {"conversions": sources, "trace": [record]}
