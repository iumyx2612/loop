"""The column-mapping subgraph's nodes (graph in agent.py): `answer` asks the model, `check` checks the answer in
code and asks again with the problems, until it passes or MAX_ATTEMPTS replies were made.

- answer: one structured-output call on state["request"]. The reply schema holds one field per sales_lines
  column in state["fields"], each a Literal of the line table's real column names + "(not in this file)", so a
  reply cannot invent a name, drop accents, forget a column or list one twice. The reply's answers replace the old
  ones for those fields only, so an answer that passed is never changed.
- check: every check (checks.py) on the merged answer. Problems → the next request is one fresh, self-contained
  message (retry_message, prompts.RETRY; user, 2026-11-07: no conversation history): the README meanings, the
  table with the evidence rows of each shared column, the problems (failed answers one per line, then the
  explanation), and only the fields in question (those that failed their column check; for a cross-column
  problem CROSS_FIELDS; for a shared column every field on it). An unreadable reply → the same request again with
  that reply and prompts.REPLY_PROBLEM after it, the same fields.
- After the last attempt: a field that failed its column check is not mapped; a cross-column or shared-column
  problem leaves its fields mapped (the check cannot tell which one is wrong). All stay in problems.
- llm_check (after check, once): a fresh call, without the conversation, that reviews the final matching of the
  fields code cannot check the meaning of: every asked field except the mapped AMOUNT_FIELDS when the cross-column
  check passed. Each field comes with what its column holds over all rows (distinct values, most common ones),
  which the 5-row table hides (biz_123: seller ← a column with the shop id on every row). The reply has the
  answer's shape (line_columns_schema of the reviewed fields: one enum per field, the column that holds it); a
  field whose column differs from the current answer is a fix. A fix is trusted, except
  that a field with a column check must still pass it, and a fix of an amount field must keep the cross-column
  check passing. A call that raises (e.g. a server timeout) is recorded as `call_failed` and changes nothing: the
  mapping stays as check left it.

What is asked: ASKED = plan_check.MAPPABLE + OPTIONAL without line_product_name (it only means something once step
3 knows whether product_name comes from a product list, so code sets it there). Only the line tables count; the
other tables come in step 3.
"""
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.plan_check import MAPPABLE, OPTIONAL
from src.datamate.analyst_agent.explore_agent.readme import readme_columns
from src.datamate.analyst_agent.explore_agent.state import ColumnSource, ExploreContext, TableRef
from src.datamate.analyst_agent.explore_agent.utils import label
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.amounts import (AMOUNT_FIELDS, MIN_FIT, _Columns,
                                                                                 check_amounts, decide_qty_base)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.amounts import problem_text as amounts_problem
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.checks import (CROSS_FIELDS, check_answer,
                                                                                check_shared_columns, evidence_rows,
                                                                                non_blank_cells)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.prompts import (COLUMN_MEANINGS, LINE_COLUMNS,
                                                                                 LLM_CHECK, NOT_HERE, REPLY_PROBLEM,
                                                                                 RETRY)
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.state import ColumnMappingState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured

TABLE_ROWS = 5  # data rows shown in the table
ASKED = tuple(c for c in MAPPABLE + OPTIONAL if c != "line_product_name")  # the reply fields, in this order
MAX_ATTEMPTS = 3  # replies per shop: the first answer + 2 retries
COMMON_VALUES = 5  # most common values shown per reviewed column (llm_check)
assert set(COLUMN_MEANINGS) == set(ASKED), "prompts.COLUMN_MEANINGS must describe exactly the asked columns"


# ---------- the first message and the reply schema ----------

def line_tables_of(shop_dir: Path, refs: list[TableRef], headers: dict | None,
                   skipped: dict | None = None) -> list[explore.Table]:
    """The line tables, carrying the filter step's rows (`skipped`): checks, grids and profiles leave them out."""
    return [t for r in refs for t in explore.tables(shop_dir, r.file, r.sheet, headers, skipped)]


def readme_meanings_text(shop_dir: Path, t: explore.Table) -> str:
    """'- **Col**: meaning' for each column of t the README describes, in header order."""
    spec = readme_columns(shop_dir, t.file, t.sheet)
    meanings = spec.meanings if spec else {}
    lines = [f"- **{c}**: {meanings[c]}" for c in t.columns if meanings.get(c)]
    return "\n".join(lines) or "(the README does not describe these columns)"


def first_rows(t: explore.Table, n: int = TABLE_ROWS) -> list[int]:
    """The first n data rows (the filter step's rows left out), numbered as in the file."""
    return t.data_numbers()[:n]


def table_text(t: explore.Table, n: int = TABLE_ROWS, rows: list[int] | None = None) -> str:
    """The header and the first n data rows of t (or the given rows, numbered as in the file), aligned like a
    printed dataframe; first column = row number."""
    rows = first_rows(t, n) if rows is None else rows
    grid = [["row", *t.columns]] + [[str(i), *(explore._cell(v) for v in t.rows[i - 1])] for i in rows]
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    sizes = [max(len(r[j]) for r in grid) for j in range(width)]
    return "\n".join(" | ".join(c.ljust(s) for c, s in zip(r, sizes)).rstrip() for r in grid)


def first_message(shop_dir: Path, t: explore.Table) -> str:
    columns = "\n".join(f"- {c}: {COLUMN_MEANINGS[c]}" for c in ASKED)
    return LINE_COLUMNS.format(readme_meanings=readme_meanings_text(shop_dir, t), table=table_text(t),
                               columns=columns, not_here=NOT_HERE)


def line_columns_schema(raw_columns: list[str], fields: tuple[str, ...] = ASKED,
                        name: str = "LineColumns") -> type[BaseModel]:
    """The reply schema for one shop: one field per sales_lines column in `fields`, each a JSON-schema enum of the
    line table's column names + NOT_HERE. The field names are the same for every shop; only the enum changes.
    A retry asks fewer fields; llm_check asks the reviewed fields (name "Review")."""
    choice = Literal[tuple(dict.fromkeys([*raw_columns, NOT_HERE]))]
    return create_model(name, **{c: (choice, Field(description=COLUMN_MEANINGS[c])) for c in fields})


# ---------- checking a whole answer ----------

@dataclass
class Checked:
    columns: list[ColumnSource] = field(default_factory=list)
    open_columns: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    retry_fields: tuple[str, ...] = ()  # the fields to ask again, in ASKED order
    checks: dict = field(default_factory=dict)  # each check's record, for the trace
    answer: dict = field(default_factory=dict)  # the answer checked, with code's fixes
    fixes: dict = field(default_factory=dict)  # field -> column or NOT_HERE that code set (amounts.py)


def check_mapping(answer: dict[str, str], tables: list[explore.Table], repair: bool = True,
                  cols: _Columns | None = None) -> Checked:
    """Every check on the whole answer (one raw column or NOT_HERE per asked field). The amount fields are code's
    (amounts.py): an answer that does not fit the equation is repaired when one closest fitting answer exists, and
    qty_base is decided by its ratio to qty; the answer is then checked again with those fixes (out.answer,
    out.fixes). `repair=False`: that second check (the amounts are checked, not repaired)."""
    cols = cols or _Columns(tables)
    first, out, retry = tables[0], Checked(answer=dict(answer)), set()
    for f in ASKED:
        raw = answer[f]
        if raw == NOT_HERE:
            out.open_columns.append(f)
            continue
        problem, rec = check_answer(f, raw, tables)
        if rec:
            out.checks[f] = rec
        if problem:
            out.problems.append(problem)
            retry.add(f)
        else:
            out.columns.append(ColumnSource(schema_column=f, file=first.file, sheet=first.sheet, raw_column=raw))
    mapped = {c.schema_column: c.raw_column for c in out.columns}
    amounts = check_amounts(mapped, tables, mapped.get("unit"), repair, cols)
    fixes = {}
    if amounts is None:
        out.checks["line_amounts"] = {"skipped": "needs qty, unit_price and line_amount"}
    else:
        out.checks["line_amounts"] = amounts.record
        fixes = {f: NOT_HERE if c is None else c for f, c in amounts.repaired.items()}
        if not fixes and not amounts.fit.ok:
            out.problems.append(amounts_problem(mapped, amounts))
            retry.update(CROSS_FIELDS)
        elif not fixes:  # the amounts fit: qty_base is code's too
            current = mapped.get("qty_base")
            qb, rec = decide_qty_base(tables, mapped["qty"], current, mapped.get("product_code") or
                                      mapped.get("product_name"), mapped.get("unit"), cols)
            out.checks["qty_base"] = rec
            if qb != current:
                fixes["qty_base"] = NOT_HERE if qb is None else qb
    if fixes and repair:
        again = check_mapping({**answer, **fixes}, tables, repair=False, cols=cols)
        again.fixes = {**fixes, **again.fixes}
        again.checks["code_fixes"] = again.fixes
        if "repair" in out.checks["line_amounts"]:  # keep the first check's repair record
            again.checks["line_amounts"] = {**again.checks["line_amounts"],
                                            "repair": out.checks["line_amounts"]["repair"],
                                            "before_repair": {k: v for k, v in out.checks["line_amounts"].items()
                                                              if k != "repair"}}
        if "qty_base" in out.checks and "qty_base" not in again.checks:
            again.checks["qty_base"] = out.checks["qty_base"]
        return again
    if fixes:  # the second check: qty_base can still change once (a repaired qty needs its own qty_base)
        out.fixes = fixes
        out.answer = {**answer, **fixes}
        out.columns = [c for c in out.columns if c.schema_column not in fixes]
        out.open_columns = [f for f in out.open_columns if f not in fixes]
        for f, c in fixes.items():
            if c == NOT_HERE:
                out.open_columns.append(f)
            else:
                out.columns.append(ColumnSource(schema_column=f, file=first.file, sheet=first.sheet, raw_column=c))
        out.open_columns = [f for f in ASKED if f in out.open_columns]
        order = {f: i for i, f in enumerate(ASKED)}
        out.columns.sort(key=lambda c: order[c.schema_column])
    # every field the model pointed at a column, also those that failed their column check
    shared, shared_retry, rec = check_shared_columns({f: c for f, c in out.answer.items()
                                                      if f in ASKED and c != NOT_HERE})
    out.checks["shared_columns"] = rec
    out.problems += shared
    retry.update(shared_retry)
    out.retry_fields = tuple(f for f in ASKED if f in retry)
    return out


# ---------- the retry message ----------

def problems_text(problems: list[str]) -> str:
    """Each problem as '- ' + its lines: the failed answers one per line, then the explanation."""
    return "\n".join(f"- {p}" for p in problems)


def retry_message(shop_dir: Path, tables: list[explore.Table], result: Checked) -> str:
    """The self-contained retry (prompts.RETRY): README meanings, the table (first rows, then for each shared
    column the rows where it and its look-alikes differ), the problems, and only the fields asked again."""
    t = tables[0]
    rows, other, notes = first_rows(t), [], []
    for column in result.checks.get("shared_columns", {}):
        alikes, where, extra = evidence_rows(tables, column)
        if not extra:
            continue
        notes.append(f"'{column}' and {' / '.join(alikes)}")  # as in test.py run 7
        if where is t:
            rows += [i for i in extra if i not in rows]
        else:  # the rows are in another split file / month sheet: its own grid, with its name
            other.append(f"\nRows of {label(where.file, where.sheet)}:\n{table_text(where, rows=extra)}")
    note = (f"its columns, its first rows, then rows further down where {' and where '.join(notes)} differ"
            if notes else "its columns and first rows")
    columns = "\n".join(f"- {f}: {COLUMN_MEANINGS[f]}" for f in result.retry_fields)
    return RETRY.format(readme_meanings=readme_meanings_text(shop_dir, t), table_note=note,
                        table=table_text(t, rows=rows) + "".join(other), problems=problems_text(result.problems),
                        not_here=NOT_HERE, columns=columns)


# ---------- the nodes ----------

def answer(state: ColumnMappingState, runtime: Runtime[ExploreContext]) -> dict:
    """One call on state["request"]: the first message, or the retry check built (a fresh call, no history)."""
    fields = tuple(state["fields"])
    schema = line_columns_schema(state["raw_columns"], fields)
    parsed, _, call = call_structured(runtime, schema, state["request"])
    attempt = state["attempts"] + 1
    rec = {"node": "answer", "attempt": attempt, "fields": "all" if fields == ASKED else list(fields), **call}
    if parsed is None:  # kept, so the next request shows the model what it sent
        return {"last_reply": rec.get("raw_reply") or "(empty reply)", "reply_error": rec["error"],
                "attempts": attempt, "log": [rec]}
    new = parsed.model_dump()
    rec["answer"] = new
    return {"last_reply": json.dumps(new, ensure_ascii=False), "answer": {**state["answer"], **new},
            "reply_error": "", "attempts": attempt, "log": [rec]}


def check(state: ColumnMappingState) -> Command[Literal["answer", "llm_check"]]:
    last = state["attempts"] >= MAX_ATTEMPTS
    if state["reply_error"]:
        rec = {"node": "check", "attempt": state["attempts"], "unreadable": True}
        update = {"log": [rec]}  # the last readable answer's result stays as it was
        feedback = REPLY_PROBLEM.format(error=state["reply_error"][:300])
        # the same request again, with the unreadable reply and what was wrong with it
        request = [*state["request"], AIMessage(state["last_reply"]), HumanMessage(feedback)]
    else:
        shop_dir = Path(state["shop_dir"])
        tables = line_tables_of(shop_dir, state["line_tables"], state["header_rows"], state.get("skipped_rows"))
        result = check_mapping(state["answer"], tables)
        rec = {"node": "check", "attempt": state["attempts"], "problems": result.problems, "checks": result.checks}
        update = {"columns": result.columns, "open_columns": result.open_columns, "problems": result.problems,
                  "checks": result.checks, "log": [rec]}
        if result.fixes:  # code repaired amount fields / decided qty_base: the answer takes them
            rec["code_fixes"] = result.fixes
            update["answer"] = result.answer
        if not result.problems:
            return Command(update=update, goto="llm_check")
        update["fields"] = list(result.retry_fields)
        if last:
            return Command(update=update, goto="llm_check")
        feedback = retry_message(shop_dir, tables, result)
        request = [HumanMessage(feedback)]  # a fresh call: no history
    if last:
        return Command(update=update, goto="llm_check")
    rec["feedback"] = feedback
    return Command(update={**update, "request": request}, goto="answer")


# ---------- llm_check: one review of the final matching, without the conversation ----------

def reviewed_fields(state: ColumnMappingState) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(locked, reviewed): when the amounts fit (amounts.py), the mapped AMOUNT_FIELDS are locked (code checked what
    they mean) and qty_base is code's (decided by its ratio to qty: locked when mapped, never reviewed); every other
    asked field is reviewed, mapped or not (an open line_discount too: a wrong fix is repaired back by code)."""
    cross = state["checks"].get("line_amounts", {})
    passed = "fit" in cross and cross["fit"] >= MIN_FIT and not cross.get("bad_units")  # the record, not the text
    qty_base_decided = passed and "qty_base" in state["checks"]
    mapped = {c.schema_column for c in state["columns"]}
    locked = tuple(f for f in ASKED if f in mapped and passed and
                   (f in AMOUNT_FIELDS or (f == "qty_base" and qty_base_decided)))
    return locked, tuple(f for f in ASKED if f not in locked and not (f == "qty_base" and qty_base_decided))


def _short(v, n: int = 40) -> str:
    s = explore._cell(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def column_profile(column: str, tables: list[explore.Table], rows: int) -> str:
    """What `column` holds over all rows of the line tables: filled cells, distinct values, the most common ones."""
    cells = non_blank_cells(tables, column)
    if not cells:
        return f"empty on all {rows:,} rows"
    counts = Counter(str(v).strip() for v in cells)
    first = {}  # one stored value per distinct text, to show it as stored
    for v in cells:
        first.setdefault(str(v).strip(), v)
    common = ", ".join(f"{_short(first[k])} ×{n:,}" for k, n in counts.most_common(COMMON_VALUES))
    return (f"filled on {len(cells):,} of {rows:,} rows, {len(counts):,} distinct value(s); "
            f"most common: {common}")


def llm_check_message(state: ColumnMappingState, tables: list[explore.Table],
                      locked: tuple[str, ...], reviewed: tuple[str, ...]) -> str:
    answer, t = state["answer"], tables[0]
    rows = sum(len(x.data_numbers()) for x in tables)
    cross = state["checks"].get("line_amounts", {})
    locked_text = (", ".join(f"{f} ← {answer[f]}" for f in locked) +
                   f": line_amount = qty × unit_price − line_discount holds on {cross['fit']:.0%} of the lines"
                   if locked else "(nothing)")
    items = []
    for f in reviewed:
        raw = answer.get(f, NOT_HERE)
        if raw == NOT_HERE:
            items.append(f"- {f} ← {NOT_HERE}   ({COLUMN_MEANINGS[f]})")
        else:
            items.append(f"- {f} ← {raw}   ({COLUMN_MEANINGS[f]})\n    {column_profile(raw, tables, rows)}")
    problems = ("\n# Our checks still report\n" + "\n".join(f"- {p}" for p in state["problems"]) + "\n"
                if state["problems"] else "")
    return LLM_CHECK.format(readme_meanings=readme_meanings_text(Path(state["shop_dir"]), t), table=table_text(t),
                            locked=locked_text, rows=rows, review="\n".join(items), problems=problems,
                            not_here=NOT_HERE)


def llm_check(state: ColumnMappingState, runtime: Runtime[ExploreContext]) -> dict:
    rec = {"node": "llm_check"}
    if not state["answer"]:  # no readable reply at all: nothing to review
        return {"log": [{**rec, "skipped": "no answer"}]}
    tables = line_tables_of(Path(state["shop_dir"]), state["line_tables"], state["header_rows"],
                            state.get("skipped_rows"))
    locked, reviewed = reviewed_fields(state)
    prompt = llm_check_message(state, tables, locked, reviewed)
    rec.update(prompt=prompt, locked=list(locked))
    # the reply has the answer's shape: one required enum field per reviewed column (no list, no free text: a list
    # that may be empty made the 27B pad `{"fixes":` with newlines until max_tokens, biz_116 / 117 / 119)
    schema = line_columns_schema(state["raw_columns"], reviewed, name="Review")
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
    except Exception as e:  # noqa: BLE001 - the review is optional: a failed call (e.g. a server timeout) must not
        # lose the mapping that check already accepted; the subgraph ends normally and the error is in the trace
        return {"log": [{**rec, "call_failed": f"{type(e).__name__}: {str(e)[:500]}", "fixes": []}]}
    rec.update(**call)
    if parsed is None:
        return {"log": [{**rec, "fixes": []}]}

    old, reply = state["answer"], parsed.model_dump()
    rec["reply"] = reply
    fixes, kept = [], {}
    for field_name in reviewed:  # a fix = a reviewed field whose column differs from the current answer
        item = {"field": field_name, "from": old.get(field_name, NOT_HERE), "to": reply[field_name]}
        if item["to"] == item["from"]:
            continue
        if item["to"] != NOT_HERE and (problem := check_answer(field_name, item["to"], tables)[0]):
            item.update(kept=False, problem=problem)  # a field with a column check must still pass it
        else:
            item["kept"] = True
            kept[field_name] = item["to"]
        fixes.append(item)
    result = check_mapping({**old, **kept}, tables)
    if any(f in AMOUNT_FIELDS for f in kept) and len(result.problems) > len(state["problems"]):
        for item in fixes:  # an amount fix that breaks the cross-column check is dropped
            if item["field"] in AMOUNT_FIELDS and item["kept"] is True:
                item.update(kept=False, problem="the cross-column check fails with it")
                kept.pop(item["field"])
        result = check_mapping({**old, **kept}, tables)
    for item in fixes:  # a kept fix that code's amount repair / qty_base decision changed back is not kept
        if item.get("kept") is True and result.answer.get(item["field"], NOT_HERE) != item["to"]:
            item.update(kept=False, problem=f"the amount check set it to {result.answer.get(item['field'])}")
            kept.pop(item["field"], None)
    rec["fixes"] = fixes
    if result.fixes:
        rec["code_fixes"] = result.fixes
    if not kept and not result.fixes:
        return {"log": [rec]}
    rec["problems"] = result.problems
    return {"answer": result.answer, "columns": result.columns, "open_columns": result.open_columns,
            "problems": result.problems, "checks": result.checks, "log": [rec]}
