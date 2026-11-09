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
5c: one structured call when there are words to ask or leftover blanks: the rulebook's §8, a one-line meaning per
status, the column with its README meaning, this shop's known words as examples, the words to map with their counts
and one row using each, and the leftover blanks with their count and two rows. The reply has one field per word
(word_1 ... word_n) and, with leftover blanks, a field `blank`; each an enum of the four statuses, so every word
gets exactly one status by construction and plan_check's status checks pass. Code cannot check the meaning: no
retry. Why the LLM decides `blank` (user, 2026-11-09): the meaning comes from the rulebook (§8: in a notes column, a
note not naming a status is a completed order), which may differ on the final test.
5d: one StatusWord per word (known and asked alike; all or nothing: with a word unanswered, statuses = []), and
blank_status (None when there are no leftover blanks or no answer).

Not here: shops without a status column (dev_012, dev_014: status in a notes column steps 2-3 did not find).
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
from src.datamate.analyst_agent.explore_agent.v2.status_words.prompts import (BLANK_BLOCK, BLANK_NOTE, KNOWN_BLOCK,
                                                                              MEANINGS, Q_BLANK, Q_WORDS,
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
                   known: dict[str, str]) -> str:
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
    if words.leftover:
        rows = [(BLANK, t, i) for t, i in words.leftover_rows]
        body.append(BLANK_BLOCK.format(n=words.leftover, where="rows that start an order" if col.order_col else "rows",
                                       note=BLANK_NOTE if col.order_col else "", k=len(rows),
                                       examples=grid_text(col, rows)))
        questions.append(Q_BLANK.format(on_first=" on an order's first line" if col.order_col else ""))
    return STATUS_WORDS.format(rulebook=rulebook_sections(rulebook, RULEBOOK_SECTIONS) or "(no rulebook)",
                               meanings="\n".join(f"- {s}: {m}" for s, m in MEANINGS.items()),
                               column=col.column, meaning=f" (README: {meaning})" if meaning else "",
                               body="\n".join(body), question="\n".join(questions))


def status_schema(words: list[str], blank: bool) -> type[BaseModel]:
    """One field per word, word_1 ... word_n (a word is not a valid field name), each an enum of the four statuses;
    the description repeats the word. `blank`: one more field for the leftover blanks."""
    choice = Literal[STATUSES]
    fields = {f"word_{n}": (choice, Field(description=repr(w))) for n, w in enumerate(words, 1)}
    if blank:
        fields[BLANK] = (choice, Field(description="the status of an order whose status cell is blank"))
    return create_model("StatusWords", **fields)


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
    else:
        words = status_words(col)
        record.update(column=col.column, table=col.where, source=col.source, order_col=col.order_col,
                      rows=words.rows, blank=words.blank, leftover_blanks=words.leftover, words=dict(words.counts))
        if not words.counts:
            record["skipped"] = "the status column is empty"
        else:
            known = {w: s for w in words.counts if use_known and (s := known_status(w)) is not None}
            asked = [w for w in words.counts if w not in known]
            record.update(known=dict(known), asked=asked)
            reply = {}
            if not asked and not words.leftover:
                record["no_call"] = "every word is known and there are no leftover blanks"
            else:
                prompt = status_message(Path(state["shop_dir"]), runtime.context.rulebook, col, words, asked, known)
                record["prompt"] = prompt
                try:
                    parsed, _, call = call_structured(runtime, status_schema(asked, bool(words.leftover)),
                                                      [HumanMessage(prompt)])
                    record.update(call)
                except Exception as e:  # noqa: BLE001 - recorded; the asked words and the blank stay unmapped
                    parsed = None
                    record["call_failed"] = f"{type(e).__name__}: {str(e)[:500]}"
                if parsed is not None:
                    reply = parsed.model_dump()
            answer = {w: reply[f"word_{n}"] for n, w in enumerate(asked, 1) if f"word_{n}" in reply}
            # all or nothing: with an asked word unanswered, statuses stay [] (plan_check wants every word mapped)
            if len(known) + len(answer) == len(words.counts):
                statuses = [StatusWord(raw=w, status=known.get(w) or answer[w]) for w in words.counts]
            blank_status = reply.get(BLANK)
    record["statuses"] = {s.raw: s.status for s in statuses}
    record["blank_status"] = blank_status
    record["seconds"] = since(start)
    return {"statuses": statuses, "blank_status": blank_status, "trace": [record]}
