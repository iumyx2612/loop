"""Check the filter step (step 0, filter_rows) without an LLM.

1. Every shop of both splits (guessed headers where the README lists no columns, as without confirm_headers): per
   table group the repeated headers, dense columns, candidates and patterns, compared with the answer key
   common.SUBTOTAL (the hand-written regex of the 44 known shops; never used by the step itself):
   - missed: answer-key rows that are not candidates (the shape rule cannot catch them)
   - mixed: patterns holding answer-key rows and other rows (one verdict would treat some wrongly)
   - extra: candidate rows the answer key does not know (real data for the LLM to keep, or rows the key misses)
2. The filter graph with a fake model that judges a group "summary" when one of its marked example rows has a
   cell the answer key matches (an ideal LLM, as far as the key knows):
   - biz_110 and dev_002: every answer-key row and every repeated header is skipped; no regex problem
   - dev_002 with an unparsable reply: only the repeated headers are skipped, the error is in the trace

Usage: .venv/Scripts/python.exe -I scripts/checks/check_filter.py [--patterns] [--show-prompt split/shop]
  --patterns     list every pattern (key, rows, answer-key rows) under its group
  --show-prompt  print the prompts of one shop (part 2's fake run of that shop)
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from langchain_core.messages import AIMessage  # noqa: E402

from src.datamate.analyst_agent.explore_agent import explore  # noqa: E402
from src.datamate.analyst_agent.explore_agent.nodes import HeaderChoice  # noqa: E402
from src.datamate.analyst_agent.explore_agent.state import ExploreContext  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2 import filter_graph  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.filter_rows import rows as R  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.filter_rows.prompts import DATA_ROW, SUMMARY_ROW  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables.nodes import (line_table_candidates,  # noqa: E402
                                                                                option_names)
from src.datamate.reference.common import SUBTOTAL  # noqa: E402

DATA = ROOT / "dataset" / "zaloAI"


def key_row(r: tuple) -> bool:
    """The answer key: a text cell the hand-written SUBTOTAL regex matches."""
    return any(isinstance(v, str) and SUBTOTAL.match(v) for v in r)


def shop_dirs() -> list[tuple[str, Path]]:
    return [(f"{split}/{d.name}", d) for split in ("train", "public_test")
            for d in sorted((DATA / split / "data").iterdir()) if d.is_dir()]


# ---------- part 1 ----------

def survey(show_patterns: bool) -> bool:
    print("# 1. Candidates and patterns per table group, against the answer key common.SUBTOTAL")
    totals = {"key": 0, "missed": 0, "extra": 0, "patterns": 0, "groups_with_calls": 0, "headers": 0, "mixed": 0}
    for name, d in shop_dirs():
        groups = line_table_candidates(d, {})
        out = []
        for g, gname in zip(groups, option_names(groups)):
            found = R.find_rows(g)
            cand = {rid: p for p in found.patterns for rid in p.rows}
            key = [(k, i) for k, i in found.data_rows if key_row(g.tables[k].rows[i - 1])]
            missed = [rid for rid in key if rid not in cand]
            extra = [rid for rid in cand if not key_row(g.tables[rid[0]].rows[rid[1] - 1])]
            mixed = []
            for p in found.patterns:
                n_key = sum(key_row(g.tables[k].rows[i - 1]) for k, i in p.rows)
                if 0 < n_key < len(p.rows):
                    mixed.append(f"{p.key[:60]} ({n_key} key / {len(p.rows)})")
            totals["key"] += len(key)
            totals["missed"] += len(missed)
            totals["extra"] += len(extra)
            totals["patterns"] += len(found.patterns)
            totals["groups_with_calls"] += bool(found.patterns)
            totals["headers"] += len(found.header_rows)
            totals["mixed"] += len(mixed)
            if not (found.header_rows or found.patterns or key):
                continue
            out.append(f"   [{gname}] {len(found.data_rows):,} rows, {len(found.header_rows)} repeated header(s), "
                       f"{len(found.dense)} dense, {found.candidates} candidate(s) in {len(found.patterns)} "
                       f"pattern(s); key {len(key)}, missed {len(missed)}, extra {len(extra)}")
            for k, i in missed[:3]:
                t = g.tables[k]
                out.append(f"      MISSED {explore._row_line(i, t.rows[i - 1])[:150]}")
            out += [f"      MIXED {m}" for m in mixed]
            if show_patterns:
                for p in found.patterns:
                    n_key = sum(key_row(g.tables[k].rows[i - 1]) for k, i in p.rows)
                    out.append(f"      - {p.key[:90]} ({len(p.rows)} rows, {n_key} key)")
        if out:
            print(f"## {name}")
            print("\n".join(out))
    print(f"\nanswer-key rows {totals['key']:,}, missed {totals['missed']}, extra candidates {totals['extra']}, "
          f"mixed patterns {totals['mixed']}; repeated headers {totals['headers']:,}; "
          f"{totals['patterns']} pattern(s) in {totals['groups_with_calls']} call(s)")
    return totals["missed"] == 0 and totals["mixed"] == 0


# ---------- part 2: the graph with a fake model ----------

class FakeModel:
    def __init__(self, unparsable: bool = False):
        self.unparsable, self.prompts = unparsable, []

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert method == "json_schema" and include_raw
        if schema is HeaderChoice:
            return _Header()
        assert schema.__name__ == "RowGroups", schema.__name__
        return _Verdicts(self, schema)


class _Header:
    def invoke(self, messages):
        guess = int(re.search(r"Our guess is row (\d+)", messages[-1].content).group(1))
        return {"raw": AIMessage(""), "parsed": HeaderChoice(header_row=guess), "parsing_error": None}


def _marked_cells(group_text: str) -> list[str]:
    cells = []
    for line in group_text.splitlines():
        if line.startswith("> "):
            for c in line.split(" | ")[1:]:
                c = c.strip()
                try:
                    c = ast.literal_eval(c) if c.startswith("'") or c.startswith('"') else c
                except (ValueError, SyntaxError):
                    pass
                cells.append(c)
    return cells


class _Verdicts:
    def __init__(self, fake, schema):
        self.fake, self.schema = fake, schema

    def invoke(self, messages):
        prompt = messages[-1].content
        self.fake.prompts.append(prompt)
        if self.fake.unparsable:
            return {"raw": AIMessage("{bad"), "parsed": None, "parsing_error": ValueError("bad json")}
        section = prompt.split("# Rows that look different", 1)[1].split("\n# Question", 1)[0]
        parts = re.split(r"^Group (\d+):", section, flags=re.MULTILINE)
        answers = {}
        for n, text in zip(parts[1::2], parts[2::2]):
            hit = any(isinstance(c, str) and SUBTOTAL.match(c) for c in _marked_cells(text))
            answers[f"group_{n}"] = SUMMARY_ROW if hit else DATA_ROW
        return {"raw": AIMessage(""), "parsed": self.schema(**answers), "parsing_error": None}


def run(split_shop: str, unparsable: bool = False) -> tuple[dict, FakeModel]:
    split, shop = split_shop.split("/")
    model = FakeModel(unparsable)
    state = filter_graph.invoke({"shop_id": shop, "shop_dir": str(DATA / split / "data" / shop)},
                                context=ExploreContext(model=model))
    return state, model


def expected(d: Path, headers: dict) -> tuple[dict, dict]:
    """Per table key: the answer-key rows and the repeated header rows."""
    key, head = {}, {}
    for g in line_table_candidates(d, headers):
        found = R.find_rows(g)
        for k, t in enumerate(g.tables):
            tk = explore.table_key(t.file, t.sheet)
            key[tk] = {i for kk, i in found.data_rows if kk == k and key_row(t.rows[i - 1])}
            head[tk] = {i for kk, i in found.header_rows if kk == k}
    return key, head


def graph_checks(show: str | None) -> bool:
    print("\n# 2. The filter graph with a fake model (an ideal judge as far as the answer key knows)")
    results = []

    def check(what: str, ok: bool) -> None:
        results.append(ok)
        print(f"{'ok  ' if ok else 'FAIL'} {what}")

    for shop in ("train/biz_110", "public_test/dev_002"):
        state, model = run(shop)
        d = Path(state["shop_dir"])
        key, head = expected(d, state["header_rows"])
        skipped = {k: set(v) for k, v in state["skipped_rows"].items()}
        n_key, n_head = sum(map(len, key.values())), sum(map(len, head.values()))
        check(f"{shop}: all {n_key} answer-key rows skipped",
              all(rows <= skipped.get(tk, set()) for tk, rows in key.items()))
        check(f"{shop}: all {n_head} repeated header rows skipped",
              all(rows <= skipped.get(tk, set()) for tk, rows in head.items()))
        rec = next(r for r in state["trace"] if r["step"] == "filter_rows")
        problems = [p for g in rec["groups"] for p in g.get("problems", [])]
        check(f"{shop}: regexes {state['subtotal_labels']} match no row kept as data", not problems)
        if problems:
            print("     " + "\n     ".join(problems))
        check(f"{shop}: {len(model.prompts)} call(s) = groups with candidates",
              len(model.prompts) == sum(bool(g["candidates"]) for g in rec["groups"]))
        if shop == show:
            print("\n\n".join(model.prompts))

    state, model = run("public_test/dev_002", unparsable=True)
    key, head = expected(Path(state["shop_dir"]), state["header_rows"])
    skipped = {k: set(v) for k, v in state["skipped_rows"].items()}
    rec = next(r for r in state["trace"] if r["step"] == "filter_rows")
    check("dev_002 unparsable reply: only the repeated headers skipped",
          all(skipped.get(tk, set()) == rows for tk, rows in head.items() if rows)
          and set(skipped) <= {tk for tk, rows in head.items() if rows})
    check("dev_002 unparsable reply: error and raw reply in the trace",
          any(g.get("error") and g.get("raw_reply") == "{bad" for g in rec["groups"]))

    print(f"\n{sum(results)}/{len(results)} checks ok")
    return all(results)


def main() -> None:
    show = sys.argv[sys.argv.index("--show-prompt") + 1] if "--show-prompt" in sys.argv else None
    survey("--patterns" in sys.argv)
    graph_checks(show)


if __name__ == "__main__":
    main()
