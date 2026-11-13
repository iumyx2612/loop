"""Step 7, map_line_kinds: which product names are not items (agreed with the user, 2026-11-10).

The plan gets two lists of product-name values (not a column): service_fee_names, rows that are revenue but not an
item (rulebook §2 "Mặt hàng": a Phí phục vụ line is never counted or ranked as an item but is revenue), and
non_revenue_names, rows that are not sales (§1 "Doanh thu một đơn": order discounts / vouchers not subtracted,
shipping not added). Revenue alone would not need the first list; item questions do (the query_agent keeps
line_kind == "product" for product measures, rankings, gross profit and the product list).

1. code (candidates.py): the top TOP_K products by order share + the products whose lines mostly have no qty, no
   price or a negative amount; names differing only in digits are one question.
2. one structured call (prompts.LINE_KINDS): the rulebook's two rows quoted, the three kinds, each candidate with
   its line count, order share and two example lines. Reply name_1 ... name_n, each an enum of the three kinds:
   every candidate gets one answer by construction. Code cannot check the meaning: no retry. A failed call leaves
   both lists empty (every row an item, as before step 7).
3. pass 2 (agreed 2026-11-11): each candidate pass 1 did not call an item gets its own call, the same prompt with
   only that name and a one-sentence reason before its kind; that kind replaces pass 1's (a failed call keeps pass
   1's). Why: on the 9B, only one name per call with a reason got Voucher / Phí ship right (prompts.WHY_LINE).
4. the plan's lists: every product-name spelling of the candidates finally "revenue, not an item" / "not revenue".
"""
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.utils import label, since
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.analyst_agent.explore_agent.v2.line_kinds.candidates import Candidates, find_candidates
from src.datamate.analyst_agent.explore_agent.v2.line_kinds.prompts import (KINDS, LINE_KINDS, REASON, RULE_ROWS,
                                                                            WHY_LINE)
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured

ITEM, FEE, NOT_REVENUE = KINDS  # the enum values, in prompts.KINDS order
NAME_PREVIEW = 5  # spellings shown per candidate


def rule_rows(rulebook: str, labels=RULE_ROWS) -> str:
    """The rulebook's table rows whose first cell is one of `labels` (bold marks ignored), as '- label: text'."""
    out = []
    for line in rulebook.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and re.sub(r"\*", "", cells[0]).strip() in labels:
            out.append(f"- {re.sub(r'[*]', '', cells[0]).strip()}: {cells[1]}")
    return "\n".join(out) or "(no rulebook rows found)"


def examples_text(c: Candidates) -> str:
    """One grid: a header of the shown line columns (+ the products table's name for code-only shops), then two
    rows per candidate, labelled name_n."""
    many = len(c.tables) > 1
    head = ["", "row", *[col for _, col in c.shown]] + (["name (products table)"] if c.extra_name else [])
    grid = [head]
    for n, cand in enumerate(c.candidates, 1):
        for t, i in cand.rows:
            r = t.rows[i - 1]
            cells = []
            for _, col in c.shown:
                j = explore.column_index(t, col)
                cells.append(explore._cell(r[j]) if j is not None and j < len(r) else "")
            if c.extra_name:
                jk = explore.column_index(t, c.key_col)
                cells.append(c.extra_name.get(value_key(r[jk]) if jk is not None and jk < len(r) else None, ""))
            grid.append([f"name_{n}", f"{label(t.file, t.sheet)} row {i}" if many else str(i), *cells])
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    sizes = [max(len(r[j]) for r in grid) for j in range(width)]
    return "\n".join(" | ".join(x.ljust(s) for x, s in zip(r, sizes)).rstrip() for r in grid)


def names_text(c: Candidates) -> str:
    out = []
    for n, cand in enumerate(c.candidates, 1):
        spell = cand.names[:NAME_PREVIEW]
        more = f" (+{len(cand.names) - NAME_PREVIEW} more)" if len(cand.names) > NAME_PREVIEW else ""
        what = repr(cand.pattern) if len(cand.keys) == 1 else \
            f"{cand.pattern!r}: {', '.join(map(repr, spell))}{more}"
        out.append(f"name_{n}: {what}: {cand.lines:,} lines, on {cand.share:.0%} of the orders")
    return "\n".join(out)


def line_kinds_message(rulebook: str, c: Candidates, why: bool = False) -> str:
    """why: pass 2's prompt (one candidate; the reason before the kind)."""
    key_note = f" (by {c.key_col!r})" if c.key_col else ""
    text = LINE_KINDS.format(rulebook=rule_rows(rulebook), kinds="\n".join(f"- {k}: {m}" for k, m in KINDS.items()),
                             key_note=key_note, names=names_text(c), examples=examples_text(c))
    return text + (WHY_LINE if why else "")


def line_kinds_schema(c: Candidates, why: bool = False) -> type[BaseModel]:
    """why: a reason_n field before each name_n (pass 2), free text in a fixed field; no length limit (a reason
    failing validation would lose a right answer), max_tokens is the guard."""
    choice = Literal[tuple(KINDS)]
    fields = {}
    for n, cand in enumerate(c.candidates, 1):
        if why:
            fields[f"reason_{n}"] = (str, Field(description=REASON.format(pattern=cand.pattern)))
        fields[f"name_{n}"] = (choice, Field(description=repr(cand.pattern)))
    return create_model("LineKinds", **fields)


def ask_alone(runtime: Runtime[ExploreContext], c: Candidates, cand) -> dict:
    """Pass 2 for one candidate: its own call, a reason before the kind. Returns the trace record of the call:
    kind / reason when the reply was read, else call_failed or the parse error."""
    one = replace(c, candidates=[cand])
    prompt = line_kinds_message(runtime.context.rulebook, one, why=True)
    record = {"prompt": prompt}
    try:
        parsed, _, call = call_structured(runtime, line_kinds_schema(one, why=True), [HumanMessage(prompt)])
        record.update(call)
    except Exception as e:  # noqa: BLE001 - recorded; pass 1's answer stays
        record["call_failed"] = f"{type(e).__name__}: {str(e)[:500]}"
        return record
    if parsed is not None:
        reply = parsed.model_dump()
        record.update(kind=reply["name_1"], reason=reply["reason_1"])
    return record


def map_line_kinds(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node."""
    start = time.perf_counter()
    record = {"step": "map_line_kinds"}
    fees: list[str] = []
    not_revenue: list[str] = []
    c = find_candidates(state)
    record.update(key_col=c.key_col, name_source=c.name_source, keys=c.keys, total=c.total,
                  candidates=[{"pattern": x.pattern, "keys": x.keys, "names": x.names, "lines": x.lines,
                               "share": round(x.share, 4), "unusual": round(x.unusual, 4), "why": x.why}
                              for x in c.candidates])
    if c.skipped:
        record["skipped"] = c.skipped
    elif c.candidates:
        prompt = line_kinds_message(runtime.context.rulebook, c)
        record["prompt"] = prompt
        try:
            parsed, _, call = call_structured(runtime, line_kinds_schema(c), [HumanMessage(prompt)])
            record.update(call)
        except Exception as e:  # noqa: BLE001 - recorded; both lists stay empty
            parsed = None
            record["call_failed"] = f"{type(e).__name__}: {str(e)[:500]}"
        if parsed is not None:
            reply = parsed.model_dump()
            first = {cand.pattern: reply.get(f"name_{n}") for n, cand in enumerate(c.candidates, 1)}
            record["answers"] = first  # pass 1
            # pass 2: every name pass 1 did not call an item, alone with a reason; its kind replaces pass 1's (also
            # "item"); a failed call keeps pass 1's
            second = {}
            final = dict(first)
            for cand in c.candidates:
                if first[cand.pattern] in (FEE, NOT_REVENUE):
                    rec = ask_alone(runtime, c, cand)
                    second[cand.pattern] = rec
                    if rec.get("kind"):
                        final[cand.pattern] = rec["kind"]
            record["recheck"] = second
            record["final"] = final
            # the record's usage = pass 1 + pass 2 (the runner sums each trace record's usage)
            total = dict(record.get("usage") or {})
            for rec in second.values():
                for k, n in (rec.get("usage") or {}).items():
                    total[k] = total.get(k, 0) + n
            record["usage"] = total
            for cand in c.candidates:
                if final[cand.pattern] == FEE:
                    fees += cand.names
                elif final[cand.pattern] == NOT_REVENUE:
                    not_revenue += cand.names
    record["service_fee_names"], record["non_revenue_names"] = fees, not_revenue
    record["seconds"] = since(start)
    return {"service_fee_names": fees, "non_revenue_names": not_revenue, "trace": [record]}
