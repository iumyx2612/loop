"""Step 5, map_statuses: the shop's own status-word mapping and what a leftover blank means, written into
state["statuses"] and state["blank_status"] (the plan's statuses and blank_status).

5a / 5b (code, words.py): the status column (step 2, else step 3's snapshot), its distinct words with counts, and
its leftover blanks (blank on a row whose order id is filled: not a forward-fill continuation line), data rows only.
Hybrid (user, 2026-11-09): a word the known-word list has (KNOWN = parse.STATUS_MAP, matched by parse.norm_text:
case, spaces and Unicode form ignored, accents kept) takes its status from the list, no LLM. Why: some words are
conventions of this dataset checked against the answer key ('Đang giao dịch' → in_delivery, 'Không giao được' →
cancelled) that no model can work out, and the unseen shops come from the same dataset. Test knob
state["known_status_words"] = False (runner flag --no-known-words): every word goes to the LLM, to measure it on
train / public test, where the list knows every word.
5c: the words call, when there are words to ask: the rulebook's §8, a one-line meaning per status, the column with
its README meaning, this shop's known words as examples, the words to map with their counts and one row using each.
The reply has one field per word (word_1 ... word_n), each an enum of the four statuses, so every word gets exactly
one status by construction and plan_check's status checks pass. Code cannot check the meaning: no retry.
5c': the blank call (split off 2026-11-11, agreed), when there are leftover blanks: the same prompt with the leftover
blanks (count, share, two rows) instead of words, the words known or just answered as examples, and a one-sentence
`reason` before `blank` (prompts.BLANK_WHY). Why: dev_011 / dev_014 on the 9B answered in_delivery without a reason
(63 % / 99 % of their lines), completed with one; the words keep bare enums (user: reason on the blank only). Why the LLM decides `blank` (user, 2026-11-09): the meaning comes from the rulebook (§8: in a notes column, a
note not naming a status is a completed order), which may differ on the final test.
5d: one StatusWord per word (known and asked alike; all or nothing: with a word unanswered, statuses = []), and
blank_status (None when there are no leftover blanks or no answer).
5e: no status column (agreed 2026-11-13, user): when neither step 2 (its open_fields included, which finds a status
inside a notes column: dev_011 / dev_014) nor step 3's snapshot found one, the blank question is still asked
(prompts.NO_COLUMN: the rulebook's §8, the meanings, the bare fact "no column holding the order's status"; reason,
then blank). statuses = []; blank_status = the answer, which write_code's stage 6 gives every line. Runs after every
step that can find a status column, so shops with one (dev_011, dev_013, dev_014) never reach it. A failed call
leaves blank_status None, as before. Why: dev_012 (repair book, Ghi chú with notes naming no status) got no status
at all, so every revenue measure was 0; rulebook §8 "Sổ không có cột trạng thái": a note not naming a status =
completed.
"""
import time
from pathlib import Path
from typing import Literal, get_args

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, create_model

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.readme import readme_columns
from src.datamate.analyst_agent.explore_agent.state import ExploreContext, Status, StatusWord
from src.datamate.analyst_agent.explore_agent.utils import rulebook_sections, since
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.status_words.prompts import (BLANK_BLOCK, BLANK_NOTE, BLANK_ORDERS,
                                                                              BLANK_REASON, BLANK_WHY, KNOWN_BLOCK,
                                                                              MEANINGS, NO_COLUMN, Q_BLANK, Q_WORDS,
                                                                              RULEBOOK_SECTIONS, STATUS_WORDS,
                                                                              WORDS_BLOCK)
from src.datamate.analyst_agent.explore_agent.v2.status_words.words import (StatusColumn, Words, status_column,
                                                                            status_words, where_text)
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured
from src.datamate.parse import STATUS_MAP, norm_text

STATUSES = get_args(Status)
KNOWN = STATUS_MAP  # norm_text(word) -> status: the words of the 44 known shops, checked against the answer key
BLANK = "blank"  # the reply field for the leftover blanks
assert set(MEANINGS) == set(STATUSES), "prompts.MEANINGS must describe exactly the four statuses"
assert set(KNOWN.values()) <= set(STATUSES), "parse.STATUS_MAP must use the plan's Status values"


def known_status(word: str) -> str | None:
    return KNOWN.get(norm_text(word))


def grid_text(col: StatusColumn, rows: list[tuple[str, explore.Table, int]]) -> str:
    """One grid: the header, then each (label, table, row number). With several tables (month sheets, split files)
    the row column also names the table."""
    many = len(col.tables) > 1
    grid = [["", "row", *col.tables[0].columns]]
    for name, t, i in rows:
        grid.append([name, where_text(t, i) if many else str(i), *(explore._cell(v) for v in t.rows[i - 1])])
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    sizes = [max(len(r[j]) for r in grid) for j in range(width)]
    return "\n".join(" | ".join(c.ljust(s) for c, s in zip(r, sizes)).rstrip() for r in grid)


def status_message(shop_dir: Path, rulebook: str, col: StatusColumn, words: Words, asked: list[str],
                   known: dict[str, str], blank: bool = True) -> str:
    """The words call: asked words, blank=False. The blank call: asked=[], blank=True (+ prompts.BLANK_WHY)."""
    t = col.tables[0]
    spec = readme_columns(shop_dir, t.file, t.sheet)
    meaning = spec.meanings.get(col.column) if spec else None
    body, questions = [], []
    if known:
        body.append(KNOWN_BLOCK.format(known="\n".join(f"- {w!r} → {s}" for w, s in known.items())))
    if asked:
        listed = "\n".join(f"word_{n}: {w!r} ({words.counts[w]:,} rows)" for n, w in enumerate(asked, 1))
        rows = [(f"word_{n}", *words.first[w]) for n, w in enumerate(asked, 1)]
        body.append(WORDS_BLOCK.format(words=listed, examples=grid_text(col, rows)))
        questions.append(Q_WORDS)
    if blank and words.leftover:
        rows = [(BLANK, t, i) for t, i in words.leftover_rows]
        body.append(BLANK_BLOCK.format(n=words.leftover, rows=words.rows, pct=round(100 * words.leftover / words.rows),
                                       orders=BLANK_ORDERS.format(m=words.leftover_orders) if col.order_col else "",
                                       note=BLANK_NOTE if col.order_col else "", k=len(rows),
                                       examples=grid_text(col, rows)))
        questions.append(Q_BLANK)
    return STATUS_WORDS.format(rulebook=rulebook_sections(rulebook, RULEBOOK_SECTIONS) or "(no rulebook)",
                               meanings="\n".join(f"- {s}: {m}" for s, m in MEANINGS.items()),
                               column=col.column, meaning=f" (README: {meaning})" if meaning else "",
                               body="\n".join(body), question="\n".join(questions))


def status_schema(words: list[str]) -> type[BaseModel]:
    """The words call: one field per word, word_1 ... word_n (a word is not a valid field name), each an enum of the
    four statuses; the description repeats the word."""
    choice = Literal[STATUSES]
    return create_model("StatusWords", **{f"word_{n}": (choice, Field(description=repr(w)))
                                          for n, w in enumerate(words, 1)})


def blank_schema() -> type[BaseModel]:
    """The blank call: reason (one sentence, first), then blank."""
    return create_model("BlankStatus",
                        reason=(str, Field(description=BLANK_REASON)),
                        blank=(Literal[STATUSES], Field(description="the status of an order whose status cell is blank")))


def ask(runtime: Runtime[ExploreContext], schema: type[BaseModel], prompt: str) -> tuple[dict, dict]:
    """One call: (the reply as a dict, {} when unreadable or failed; the trace record of the call)."""
    rec = {"prompt": prompt}
    try:
        parsed, _, call = call_structured(runtime, schema, [HumanMessage(prompt)])
        rec.update(call)
    except Exception as e:  # noqa: BLE001 - recorded; what it asked stays unmapped
        rec["call_failed"] = f"{type(e).__name__}: {str(e)[:500]}"
        return {}, rec
    return (parsed.model_dump() if parsed is not None else {}), rec


def map_statuses(state: WorkflowState, runtime: Runtime[ExploreContext]) -> dict:
    """The parent graph's node."""
    start = time.perf_counter()
    use_known = state.get("known_status_words", True)
    record = {"step": "map_statuses", "known_words": use_known}
    statuses: list[StatusWord] = []
    blank_status = None
    col = status_column(state)
    if col is None:
        why = (state.get("missing_columns") or {}).get("status", "no status column found")
        record["skipped"] = f"no status column: {why}"
        # 5e: still ask what an order with no written status is (the rulebook decides)
        prompt = NO_COLUMN.format(rulebook=rulebook_sections(runtime.context.rulebook, RULEBOOK_SECTIONS)
                                  or "(no rulebook)",
                                  meanings="\n".join(f"- {s}: {m}" for s, m in MEANINGS.items()))
        blank_reply, call = ask(runtime, blank_schema(), prompt)
        blank_status = blank_reply.get(BLANK)
        record["blank_call"] = {**call, "no_status_column": True, "reason": blank_reply.get("reason"),
                                "blank": blank_status}
        record["usage"] = call.get("usage") or {}
    else:
        words = status_words(col)
        record.update(column=col.column, table=col.where, source=col.source, order_col=col.order_col,
                      rows=words.rows, blank=words.blank, leftover_blanks=words.leftover, leftover_orders=words.leftover_orders,
                      words=dict(words.counts))
        if not words.counts:
            record["skipped"] = "the status column is empty"
        else:
            known = {w: s for w in words.counts if use_known and (s := known_status(w)) is not None}
            asked = [w for w in words.counts if w not in known]
            record.update(known=dict(known), asked=asked)
            shop_dir, rulebook = Path(state["shop_dir"]), runtime.context.rulebook
            if not asked and not words.leftover:
                record["no_call"] = "every word is known and there are no leftover blanks"
            # the words call (unknown words only; bare enums, no reason): its record stays at the top level
            reply = {}
            if asked:
                reply, call = ask(runtime, status_schema(asked),
                                  status_message(shop_dir, rulebook, col, words, asked, known, blank=False))
                record.update(call)
            answer = {w: reply[f"word_{n}"] for n, w in enumerate(asked, 1) if f"word_{n}" in reply}
            # all or nothing: with an asked word unanswered, statuses stay [] (plan_check wants every word mapped)
            if len(known) + len(answer) == len(words.counts):
                statuses = [StatusWord(raw=w, status=known.get(w) or answer[w]) for w in words.counts]
            # the blank call (leftover blanks only; a reason first): the words answered above are shown as known
            if words.leftover:
                blank_reply, call = ask(runtime, blank_schema(),
                                        status_message(shop_dir, rulebook, col, words, [], {**known, **answer})
                                        + BLANK_WHY)
                blank_status = blank_reply.get(BLANK)
                record["blank_call"] = {**call, "reason": blank_reply.get("reason"), "blank": blank_status}
                usage = dict(record.get("usage") or {})
                for k, v in (call.get("usage") or {}).items():
                    usage[k] = usage.get(k, 0) + v
                record["usage"] = usage  # both calls (the runner sums each record's usage)
    record["statuses"] = {s.raw: s.status for s in statuses}
    record["blank_status"] = blank_status
    record["seconds"] = since(start)
    return {"statuses": statuses, "blank_status": blank_status, "trace": [record]}
