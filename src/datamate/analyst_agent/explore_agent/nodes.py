"""The fixed steps the explore_agent's workflow starts with:

    START → confirm_headers → prepare → (the workflow's steps, v2/agent.py)

- confirm_headers: for xlsx tables the README does not describe, one small LLM call per group of tables that
  share a header (structured output: the header row). Tables the README describes, csv and sqlite need none.
- prepare: no LLM. Puts the README and the file list in the state; the later prompts show them.
Both write only ShopState fields (state.py).
"""
import time
from collections import Counter
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.prompts import HEADER
from src.datamate.analyst_agent.explore_agent.state import ExploreContext, ShopState
from src.datamate.analyst_agent.explore_agent.utils import label, since, usage
from src.datamate.parse import is_blank


class HeaderChoice(BaseModel):
    header_row: int = Field(description="The row number of the column header.")


def _valid_header(t: explore.Table, row: int) -> bool:
    return 1 <= row <= min(explore.HEADER_SCAN_ROWS, len(t.rows)) and any(not is_blank(v) for v in t.rows[row - 1])


def confirm_headers(state: ShopState, runtime: Runtime[ExploreContext]) -> dict:
    start = time.perf_counter()
    shop_dir = Path(state["shop_dir"])
    headers = dict(state.get("header_rows") or {})
    llm = None  # built only when a shop needs it: most shops' headers all come from the README
    checked = []
    for group in explore.table_groups(shop_dir, headers):
        t = group.first
        if not group.needs_header_check or not t.rows:
            continue
        if llm is None:
            llm = runtime.context.model.with_structured_output(HeaderChoice, method="json_schema", include_raw=True)
        rows = explore.peek(shop_dir, t.file, t.sheet, start=1, n=explore.HEADER_SCAN_ROWS, headers=headers)
        result = llm.invoke([HumanMessage(HEADER.format(guess=t.header_row, rows=rows))])
        answer = result["parsed"].header_row if result["parsed"] is not None else None
        row = answer if answer is not None and _valid_header(t, answer) else t.header_row  # bad answer: keep guess
        for member in group.tables:
            headers[explore.table_key(member.file, member.sheet)] = row
        checked.append({"tables": [label(m.file, m.sheet) for m in group.tables], "guess": t.header_row,
                        "answer": answer, "used": row, "usage": usage(result["raw"])})
    total = Counter()
    for c in checked:
        total.update(c["usage"])
    record = {"step": "confirm_headers", "seconds": since(start), "checked": checked, "usage": dict(total),
              "header_rows": headers}
    return {"header_rows": headers, "trace": [record]}


def prepare(state: ShopState) -> dict:
    shop_dir = Path(state["shop_dir"])
    readme, files = explore.read_readme(shop_dir), explore.list_files(shop_dir, state["header_rows"])
    record = {"step": "prepare", "readme_chars": len(readme), "files": files}
    return {"readme": readme, "files": files, "trace": [record]}
