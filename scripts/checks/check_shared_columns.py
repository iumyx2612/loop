"""Replay saved explore runs through the shared-column checks (no LLM; 2026-11-11):
- llm_check: which of the recorded kept fixes column_mapping.nodes.shared_fixes would now drop;
- snapshot: which recorded answers fill_missing_schema.nodes.check_snapshot would now send to the retry, which
  retried fields the guard after the retry would fail (moved_onto_taken), and whether filled / failed stay as
  recorded otherwise.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_shared_columns.py <run folder> ... [--prompt]
(--prompt prints the snapshot retry message that would be sent; for a recorded retry, the one its first answer
would now get)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")
from src.datamate.analyst_agent.explore_agent.state import ColumnSource  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import shared_fixes  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.prompts import NOT_HERE  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.attach import shop_groups  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.nodes import (check_snapshot,  # noqa: E402
                                                                                    known_columns,
                                                                                    moved_onto_taken,
                                                                                    retry_snapshot_message)
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.checks import check_shared_columns  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.utils import load_state  # noqa: E402

show_prompt = "--prompt" in sys.argv
for folder in [a for a in sys.argv[1:] if not a.startswith("--")]:
    print(f"########## {folder}")
    for p in sorted(Path(folder).glob("*.json")):
        if p.name.startswith("_"):
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        state = load_state(d["state"])
        trace = [r for r in state.get("trace", []) if isinstance(r, dict)]
        out = []
        # --- llm_check: the mapping before the review = the final one with the kept fixes reverted
        s2 = next((r for r in trace if isinstance(r.get("llm_check"), dict)), None)
        if s2:
            fixes = [f for f in s2["llm_check"].get("fixes", []) if f.get("kept") is True]
            if fixes:
                fixed = {f["field"] for f in fixes}
                before = {k: v for k, v in s2["columns"].items() if k not in fixed}
                before.update({f["field"]: f["from"] for f in fixes if f["from"] != NOT_HERE})
                cols = [ColumnSource(schema_column=k, file="x", sheet=None, raw_column=v) for k, v in before.items()]
                dropped = shared_fixes(cols, {f["field"]: f["to"] for f in fixes})
                out.append(f"  llm_check kept fixes {[(f['field'], f['from'], f['to']) for f in fixes]}"
                           f" -> now dropped: {dropped or 'none'}")
        # --- snapshot: the checks again on each recorded answer
        snap = next((r for r in trace if r.get("step") == "snapshot"), None)
        if snap and snap.get("snapshots"):
            groups = shop_groups(state)
            links = {a["table"]: {k["anchor"]: k["column"] for k in a["links"]}
                     for a in state.get("attached") or [] if a.get("round", 0) > 0}
            for s in snap["snapshots"]:
                if not s.get("answer"):
                    continue
                g, linked = groups[s["table"]], links.get(s["table"], {})
                filled, failed, problems, retry, shared = check_snapshot(s["answer"], g, linked)
                first = (s.get("retry") or {}).get("first_answer")
                moved = {}
                if first:  # a recorded retry: the guard on its result (moved_onto_taken)
                    f_filled, _, _, f_retry, _ = check_snapshot(first, g, linked)
                    taken = {**linked, **{f: c for f, c in f_filled.items() if f not in f_retry}}
                    moved = moved_onto_taken(filled, f_retry, taken)
                    failed.update(moved)
                    filled = {f: c for f, c in filled.items() if f not in moved}
                    problems, _, shared = check_shared_columns({**linked, **filled})
                same = filled == s["filled"] and set(failed) == set(s["failed"])
                if problems or moved or not same:
                    out.append(f"  snapshot [{s['table']}] linked={linked} retry={list(retry)} "
                               f"filled/failed as recorded: {same}")
                    out += [f"      {x.splitlines()[0]}" for x in problems]
                    out += [f"      moved onto a taken column -> failed: {f}: {why}" for f, why in moved.items()]
                    if show_prompt and first:  # the retry prompt the recorded first answer would now get
                        f_filled, _, f_problems, f_retry, f_shared = check_snapshot(first, g, linked)
                        if f_retry:
                            out.append(retry_snapshot_message(Path(state["shop_dir"]), g, f_problems, f_shared,
                                                              f_retry, known_columns(linked, f_filled, f_retry)))
                    elif show_prompt and retry:
                        out.append(retry_snapshot_message(Path(state["shop_dir"]), g, problems, shared, retry,
                                                          known_columns(linked, filled, retry)))
        if out:
            print(f"== {p.stem}")
            print("\n".join(out))
