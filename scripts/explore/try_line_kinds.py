"""Step 7 (line kinds) on chosen names only: the real step 7 prompt and reply schema, with every other candidate
left out. For testing why the 9B answers dev_013's 'Voucher' as "revenue, not an item".

Loads a shop's saved state (after step 2), finds step 7's candidates with the real code (line_kinds.candidates), keeps
the ones whose pattern is in --names, builds the prompt with line_kinds.nodes.line_kinds_message and calls the model
--repeat times. Prints the prompt once, then each answer. Loads .env (API key / endpoint): the user runs it.

Usage:
  .venv/Scripts/python.exe scripts/explore/try_line_kinds.py --model qwen/qwen3.5-9b
  .venv/Scripts/python.exe scripts/explore/try_line_kinds.py --names Voucher "Phí ship" --repeat 3 --model qwen/qwen3.5-9b
  .venv/Scripts/python.exe scripts/explore/try_line_kinds.py --shops dev_013 dev_007 dev_008 --all-names --variants base why --model qwen/qwen3.5-9b
"""
import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "explore"))  # v2_runner (not on sys.path under -I)

from dotenv import load_dotenv  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

import v2_runner  # noqa: E402  (also sets stdout to utf-8)
from typing import Literal  # noqa: E402

from pydantic import Field, create_model  # noqa: E402

from src.datamate.analyst_agent.explore_agent.v2.line_kinds.candidates import find_candidates  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.line_kinds.nodes import (examples_text, names_text,  # noqa: E402
                                                                          rule_rows)
from src.datamate.analyst_agent.explore_agent.v2.line_kinds.prompts import KINDS, LINE_KINDS  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.utils import call_structured, load_state  # noqa: E402

load_dotenv()

# Prompt variants (experiment only; the real step 7 prompt is "base"). "kinds": enum value -> meaning, in the order
# shown and offered; the rest of the prompt is the real LINE_KINDS. "why": the reply first holds a one-sentence
# reason per name (diagnosis only: free text stays out of the real step). "binary": one yes/no question instead.
ITEM_M, FEE_M, NOT_M = KINDS.values()
FEE_M_NO_BILL = FEE_M.replace("charged on the bill and part of", "part of")
NOT_M_NO_WHOLE = NOT_M.replace("a discount or voucher on the whole order", "a discount or voucher")
VARIANTS = {
    "base": {"kinds": dict(KINDS)},
    # round 1 (2026-11-11): none flipped Voucher
    "no_bill": {"kinds": {"item": ITEM_M, "revenue, not an item": FEE_M_NO_BILL, "not revenue": NOT_M}},
    "labels": {"kinds": {"item": ITEM_M, "other revenue": FEE_M, "not revenue": NOT_M}},
    "labels_no_bill": {"kinds": {"item": ITEM_M, "other revenue": FEE_M_NO_BILL, "not revenue": NOT_M}},
    "order": {"kinds": {"not revenue": NOT_M, "revenue, not an item": FEE_M, "item": ITEM_M}},
    # round 2: why does it think so; does "on the whole order" exclude a voucher written as its own line
    "why": {"kinds": dict(KINDS), "why": True},
    "no_whole_order": {"kinds": {"item": ITEM_M, "revenue, not an item": FEE_M, "not revenue": NOT_M_NO_WHOLE}},
    "binary": {"binary": True},
    "binary_why": {"binary": True, "why": True},
}
ROUND_1 = ["base", "no_bill", "labels", "labels_no_bill", "order"]

WHY_Q = "\nFor each name, first write one short sentence why (reason_1, ...), then its kind (name_1, ...)."

BINARY = """A shop records its sales lines in a table: each line names something on a customer's bill.

# The rules (from the rulebook)
{rulebook}

# The line
{names}

Two lines of it:
{examples}

# Question
By the rules above, is a line named {pattern!r} part of the order's revenue (doanh thu)? Answer yes or no."""


def message(rulebook: str, c, spec: dict) -> str:
    """nodes.line_kinds_message with its own kinds (the real one reads prompts.KINDS), or the yes/no question."""
    if spec.get("binary"):
        text = BINARY.format(rulebook=rule_rows(rulebook), names=names_text(c), examples=examples_text(c),
                             pattern=c.candidates[0].pattern)
    else:
        key_note = f" (by {c.key_col!r})" if c.key_col else ""
        text = LINE_KINDS.format(rulebook=rule_rows(rulebook),
                                 kinds="\n".join(f"- {k}: {m}" for k, m in spec["kinds"].items()),
                                 key_note=key_note, names=names_text(c), examples=examples_text(c))
    return text + (WHY_Q if spec.get("why") else "")


def schema(c, spec: dict):
    """nodes.line_kinds_schema with its own enum values (+ a reason field before each answer with "why")."""
    choice = Literal[("yes", "no")] if spec.get("binary") else Literal[tuple(spec["kinds"])]
    fields = {}
    for n, x in enumerate(c.candidates, 1):
        if spec.get("why"):
            fields[f"reason_{n}"] = (str, Field(description=f"one short sentence: why, for {x.pattern!r}"))
        fields[f"name_{n}"] = (choice, Field(description=repr(x.pattern)))
    return create_model("LineKinds", **fields)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--run", default="runs/explore/v2/9B/public_test_2", help="run folder with the shop's saved state")
    p.add_argument("--shops", nargs="+", default=["dev_013"])
    p.add_argument("--names", nargs="+", default=["Voucher"], help="candidate patterns to keep, as step 7 names them")
    p.add_argument("--all-names", action="store_true",
                   help="keep every candidate of the shop, as the real step 7 call does (ignores --names)")
    p.add_argument("--one-by-one", action="store_true", help="one call per kept name instead of one call for all")
    p.add_argument("--repeat", type=int, default=3, help="calls with the same prompt")
    p.add_argument("--variants", nargs="+", default=[v for v in VARIANTS if v not in ROUND_1[1:]],
                   choices=list(VARIANTS), help="prompt variants to try (default: base + the round-2 ones)")
    p.add_argument("--show-prompts", action="store_true", help="print every variant's prompt, not only base's")
    v2_runner.add_model_args(p)
    args = p.parse_args()

    model = v2_runner.make_model(args)
    summary = []
    for shop in args.shops:
        summary += run_shop(args, model, shop)
    print("=" * 30, "SUMMARY (per shop, variant and name: the answer of each call)")
    for shop, v, rows in summary:
        print(f"--- {shop} [{v}]")
        width = max((len(name) for name, _ in rows), default=0)
        for name, answers in rows:
            if any("(" in a for a in answers):  # answers with reasons: one per line
                print(f"  {name}")
                for a in answers:
                    print(f"      {a}")
            else:
                print(f"  {name:{width}}  {' | '.join(answers)}")


def run_shop(args, model, shop: str) -> list:
    data = json.loads((ROOT / args.run / f"{shop}.json").read_text(encoding="utf-8"))
    state = load_state(data["state"])
    c = find_candidates(state)
    if args.all_names:
        kept = list(c.candidates)
    else:
        kept = [x for x in c.candidates if x.pattern in args.names]
        missing = set(args.names) - {x.pattern for x in kept}
        if missing:
            raise SystemExit(f"not among {shop}'s candidates: {sorted(missing)}; "
                             f"candidates are {[x.pattern for x in c.candidates]}")
    if not kept:
        print("=" * 30, f"{shop}: no candidates ({c.skipped or 'none found'})")
        return []
    c = replace(c, candidates=kept)

    ctx = v2_runner.context_for(model, data["split"])
    runtime = SimpleNamespace(context=ctx)  # call_structured only reads runtime.context.model
    # --one-by-one: one call per name (each its own prompt, as name_1); else one call with every kept name
    groups = [[x] for x in kept] if args.one_by_one else [kept]
    out = []
    for v in args.variants:
        spec = VARIANTS[v]
        per_name = {x.pattern: [] for x in kept}
        for g, group in enumerate(groups):
            cg = replace(c, candidates=group)
            prompt = message(ctx.rulebook, cg, spec)
            if g == 0 and (v == "base" or spec.get("binary") or args.show_prompts):
                print("=" * 30, f"{shop} PROMPT [{v}]" + (" (the first name's; the others differ in their name and "
                                                          "rows)" if args.one_by_one else ""))
                print(prompt)
            elif g == 0:  # only what differs from base: the kinds block (+ the why line)
                print("=" * 30, f"{shop} [{v}] kinds:")
                print("\n".join(f"- {k}: {m}" for k, m in spec["kinds"].items()) + (WHY_Q if spec.get("why") else ""))
            for n in range(1, args.repeat + 1):
                parsed, _, call = call_structured(runtime, schema(cg, spec), [HumanMessage(prompt)])
                which = f" {group[0].pattern!r}" if args.one_by_one else ""
                print(f"  [{shop} {v}{which}] call {n}: usage {call.get('usage')}, stop {call.get('stop_reason')}")
                if call.get("error") or parsed is None:
                    print("    error:", call.get("error"), "| raw:", call.get("raw_reply"))
                    for x in group:
                        per_name[x.pattern].append("(no reply)")
                    continue
                reply = parsed.model_dump()
                for i, x in enumerate(group, 1):
                    answer = reply[f"name_{i}"]
                    if spec.get("why"):
                        answer = f"{answer} ({reply[f'reason_{i}']})"
                    per_name[x.pattern].append(answer)
                    print(f"    {x.pattern!r}: {answer}")
        out.append((shop, v, list(per_name.items())))
    return out


if __name__ == "__main__":
    main()
