"""Replay saved runs through step 5's prompt builder (no LLM; 2026-11-11, the blank-block fix for dev_013).

Per shop: the status column, its blanks / leftover blanks / leftover orders, then
- no leftover blanks: whether the rebuilt prompt equals the recorded one (it should: the change only touches the
  blank block), "no call" when no prompt was recorded;
- leftover blanks: the recorded blank block (old code) and the rebuilt one (new code), and whether the rest of the
  prompt is unchanged.
The rebuilt prompt uses the run's own known_status_words setting, so the asked words match the recorded call.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_status_prompts.py <run folder> ... [--prompt]
(--prompt prints the whole rebuilt prompt of shops with leftover blanks)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from src.datamate.analyst_agent.explore_agent.v2.planner.status_words.nodes import known_status, status_message  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.status_words.words import status_column, status_words  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.utils import load_state  # noqa: E402

BLANK_START = "The column is also blank on"
QUESTION = "\n# Question"


def split_blank(prompt: str) -> tuple[str, str]:
    """(the prompt without its blank block, the blank block); the block runs from BLANK_START to the question."""
    i = prompt.find(BLANK_START)
    if i < 0:
        return prompt, ""
    j = prompt.find(QUESTION, i)
    return prompt[:i] + prompt[j:], prompt[i:j].rstrip()


show_prompt = "--prompt" in sys.argv
for folder in [a for a in sys.argv[1:] if not a.startswith("--")]:
    print(f"########## {folder}")
    for p in sorted(Path(folder).glob("*.json")):
        if p.name.startswith("_"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        state = load_state(d["state"])
        shop = d.get("shop", p.stem)
        rec = next((r for r in state.get("trace", []) if isinstance(r, dict) and r.get("step") == "map_statuses"),
                   None)
        if rec is None:
            print(f"== {shop}: no step 5 in this run")
            continue
        col = status_column(state)
        if col is None:
            print(f"== {shop}: no status column ({rec.get('skipped', '')})")
            continue
        words = status_words(col)
        print(f"== {shop}: column {col.column!r}, order id {col.order_col!r}: {words.rows:,} rows, "
              f"{words.blank:,} blank, {words.leftover:,} leftover blanks in {words.leftover_orders:,} orders")
        if not words.counts:
            print("   the status column is empty: no call")
            continue
        use_known = rec.get("known_words", True)
        known = {w: s for w in words.counts if use_known and (s := known_status(w)) is not None}
        asked = [w for w in words.counts if w not in known]
        old = rec.get("prompt")
        if not asked and not words.leftover:
            print("   no call (every word known, no leftover blanks)" + ("" if old is None else
                                                                            "  !! the run recorded a prompt"))
            continue
        rulebook = (Path(state["shop_dir"]).parents[1] / "rulebook.md").read_text(encoding="utf-8")
        new = status_message(Path(state["shop_dir"]), rulebook, col, words, asked, known)
        if old is None:
            print("   !! the run made no call; the new code would")
            continue
        old_rest, old_block = split_blank(old)
        new_rest, new_block = split_blank(new)
        if not words.leftover:
            print("   prompt unchanged" if old == new else "   !! prompt CHANGED without leftover blanks")
            continue
        print("   rest of the prompt " + ("unchanged" if old_rest == new_rest else "!! CHANGED"))
        print("   --- old blank block")
        print("\n".join("   " + line for line in old_block.splitlines()))
        print("   --- new blank block")
        print("\n".join("   " + line for line in new_block.splitlines()))
        if show_prompt:
            print("   --- whole new prompt")
            print(new)
