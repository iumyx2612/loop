"""Check workflow step 1 (choose_line_tables, explore_agent v2) without an LLM.

1. Every shop of both splits: the options step 1 would show (no header confirmation here: guessed headers are
   used where the README lists no columns), whether it calls the LLM, and the prompt size. Read this list by hand:
   it is what the model chooses from.
2. The v2 graph with a fake model:
   - biz_117: the fake picks the SoBanHang option -> line_tables = its 17 month sheets; the reply schema's enum is
     exactly the option names; the prompt holds the README and every option
   - biz_122: picks DonHang_ChiTiet -> 19 sheets over the split files _1 and _2026
   - biz_117 with an unparsable reply -> line_tables = [], error in the trace
   - a shop with one option (if any) -> chosen without an LLM call

Usage: .venv/Scripts/python.exe -I scripts/checks/check_v2_line_tables.py [--show-prompt split/shop]
"""
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
from src.datamate.analyst_agent.explore_agent.v2 import explore_agent  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables.nodes import (line_table_candidates, option_names,  # noqa: E402
                                                               options_text)
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables.prompts import LINE_TABLES  # noqa: E402

DATA = ROOT / "dataset" / "zaloAI"
UNPARSABLE = "unparsable"


class FakeModel:
    """Header check: keeps the guess. Step 1: picks the first option containing `pick`, or replies unparsably.
    Remembers the step-1 prompts and the enum of the reply schema."""

    def __init__(self, pick: str):
        self.pick, self.prompts, self.enums = pick, [], []

    def with_structured_output(self, schema, method=None, include_raw=False):
        assert method == "json_schema" and include_raw
        if schema is HeaderChoice:
            return _Header()
        if "option" not in schema.model_fields:  # step 2 (map_line_columns): not checked here
            return _Unparsable()
        self.enums.append(list(schema.model_fields["option"].annotation.__args__))
        return _Choice(self, schema)


class _Header:
    def invoke(self, messages):
        guess = int(re.search(r"Our guess is row (\d+)", messages[-1].content).group(1))
        return {"raw": AIMessage(""), "parsed": HeaderChoice(header_row=guess), "parsing_error": None}


class _Unparsable:
    def invoke(self, messages):
        return {"raw": AIMessage("{bad"), "parsed": None, "parsing_error": ValueError("bad json")}


class _Choice:
    def __init__(self, fake, schema):
        self.fake, self.schema = fake, schema

    def invoke(self, messages):
        self.fake.prompts.append(messages[-1].content)
        if self.fake.pick == UNPARSABLE:
            return {"raw": AIMessage("{bad"), "parsed": None, "parsing_error": ValueError("bad json")}
        option = next(o for o in self.fake.enums[-1] if self.fake.pick in o)
        return {"raw": AIMessage(""), "parsed": self.schema(option=option), "parsing_error": None}


def shop_dirs() -> list[tuple[str, Path]]:
    out = []
    for split in ("train", "public_test"):
        for d in sorted((DATA / split / "data").iterdir()):
            if d.is_dir():
                out.append((f"{split}/{d.name}", d))
    return out


def survey(show: str | None) -> str | None:
    """Part 1; returns one shop with a single option (or None)."""
    print("# 1. Options per shop (guessed headers where the README lists no columns)")
    single = None
    for name, d in shop_dirs():
        groups = line_table_candidates(d, {})
        names = option_names(groups)
        if len(groups) == 1:
            single = single or name
            print(f"## {name}: 1 option, no LLM call: {names[0]}")
        else:
            prompt = LINE_TABLES.format(readme=explore.read_readme(d), options=options_text(groups, names))
            print(f"## {name}: {len(groups)} options, prompt {len(prompt):,} chars")
            if name == show:
                print(prompt)
        for n in names if len(groups) > 1 else []:
            print(f"   - {n}")
    return single


def run(split_shop: str, pick: str) -> tuple[dict, FakeModel]:
    split, shop = split_shop.split("/")
    model = FakeModel(pick)
    state = explore_agent.invoke({"shop_id": shop, "shop_dir": str(DATA / split / "data" / shop)},
                                 context=ExploreContext(model=model))
    return state, model


def step1(state: dict) -> dict:
    return next(r for r in state["trace"] if r["step"] == "choose_line_tables")


def main() -> None:
    show = sys.argv[sys.argv.index("--show-prompt") + 1] if "--show-prompt" in sys.argv else None
    single = survey(show)
    print("\n# 2. The v2 graph with a fake model")
    results = []

    def check(what: str, ok: bool) -> None:
        results.append(ok)
        print(f"{'ok  ' if ok else 'FAIL'} {what}")

    state, model = run("train/biz_117", "SoBanHang")
    rec = step1(state)
    sheets = [t.sheet for t in state["line_tables"]]
    check("biz_117: 17 month sheets of SoBanHang.xlsx chosen",
          len(sheets) == 17 and {t.file for t in state["line_tables"]} == {"SoBanHang.xlsx"})
    check("biz_117: one LLM call, its enum = the option names", len(model.prompts) == 1
          and model.enums[0] == rec["options"])
    check("biz_117: the prompt holds the README and every option",
          state["readme"] in model.prompts[0] and all(f"Option: {o}" in model.prompts[0] for o in rec["options"]))
    check("biz_117: trace has answer, line_tables, usage", rec.get("answer") and rec["line_tables"]
          and "usage" in rec and "error" not in rec)

    state, model = run("train/biz_122", "DonHang_ChiTiet")
    files = {t.file for t in state["line_tables"]}
    check("biz_122: 19 sheets over DonHang_ChiTiet_1.xlsx + DonHang_ChiTiet_2026.xlsx",
          len(state["line_tables"]) == 19 and files == {"DonHang_ChiTiet_1.xlsx", "DonHang_ChiTiet_2026.xlsx"})

    state, model = run("train/biz_117", UNPARSABLE)
    rec = step1(state)
    check("unparsable reply: line_tables = [], error and raw reply in the trace",
          state["line_tables"] == [] and "error" in rec and rec.get("raw_reply") == "{bad")

    if single:
        state, model = run(single, UNPARSABLE)  # any LLM call here would leave line_tables empty
        rec = step1(state)
        check(f"{single}: one option, chosen without an LLM call",
              not model.prompts and len(state["line_tables"]) >= 1 and "skipped" in rec)
    else:
        print("(no shop with a single option: that path is not exercised)")

    print(f"\n{sum(results)}/{len(results)} checks ok")


if __name__ == "__main__":
    main()
