"""What the v2 runners share (run_sales_lines_v2.py, run_column_mapping_v2.py): the model, running one graph on
one shop's state with a file per shop, the console output and the summary.

One file per shop, <out_dir>/<shop>.json:
  shop, split, graph, finished, seconds, error, usage and steps (of this run only),
  summary  the step outputs so far (line_tables, columns, open_columns, column_problems)
  state    the whole graph state (v2/utils.dump_state), incl. the trace of every run so far: a later runner
           loads it and goes on from there
The file is rewritten after every step ("finished": false until the run ends), so a stopped run keeps its trace.
Each new trace record is printed as it arrives (without the long prompt, which is in the file), headed
"--- [<shop>] <step> ---"; then a summary per shop.
"""
import datetime as dt
import json
import os
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which cannot print Vietnamese

from langchain_anthropic import ChatAnthropic  # noqa: E402,F401
from langchain_openai import ChatOpenAI  # noqa: E402

from src.datamate.analyst_agent.explore_agent.state import ExploreContext  # noqa: E402
from src.datamate.analyst_agent.explore_agent.utils import label  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.utils import dump_state, load_state  # noqa: E402

DATA = ROOT / "dataset" / "zaloAI"
_print_lock = threading.Lock()  # shops run in parallel; keep each block in one piece


def add_model_args(p) -> None:
    p.add_argument("--model", default="claude-sonnet-5-5")
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--temperature", type=float, default=0.0, help="0: the same prompt gets the same answer")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--out-dir", default=None)


def make_model(args):
    return ChatOpenAI(
        model=args.model, max_tokens=args.max_tokens, temperature=args.temperature,
        base_url=os.environ["OPENROUTER_BASE_URL"],
        api_key=os.environ["OPENROUTER_API_KEY"],   # or keep using OPENAI_API_KEY from .env
        extra_body={
            # two bf16 hosts of qwen3.5-9b (user, 2026-11-11, after 429 "rate-limited upstream" on PrimeIntellect):
            # tried in `order`; `only` keeps the fallback to these two (no fp4 / fp8 / unknown hosts), so answers
            # stay bf16. OpenRouter docs: with fallbacks on, "the retry is picked the same way from the providers
            # that are left"; whether a 429 counts as a failure there is not stated
            "provider": {"order": ["primeintellect/bf16", "parasail/bf16"],
                         "only": ["primeintellect/bf16", "parasail/bf16"],
                         "allow_fallbacks": True, "require_parameters": True},
            "reasoning": {"enabled": False},
        },
    )
    # return ChatOpenAI(model=args.model, max_tokens=args.max_tokens, temperature=args.temperature)
    # return ChatAnthropic(model=args.model, max_tokens=args.max_tokens, temperature=args.temperature)


def context_for(model, split: str) -> ExploreContext:
    return ExploreContext(model=model, rulebook=(DATA / split / "rulebook.md").read_text(encoding="utf-8"))


def out_dir_for(args, suffix: str) -> Path:
    out = Path(args.out_dir) if args.out_dir else \
        ROOT / "runs" / "explore" / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + suffix)
    out.mkdir(parents=True, exist_ok=True)
    return out


def write_run(out_dir: Path, info: dict) -> None:
    (out_dir / "_run.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")


def new_state(split: str, shop: str) -> dict:
    return {"shop_id": shop, "shop_dir": str(DATA / split / "data" / shop)}


def load_saved(paths: list[str], shops: list[str] | None) -> list[tuple[str, str, dict]]:
    """(split, shop, state) from saved shop files; a path is a shop file or a run folder (all its shop files)."""
    files = []
    for p in map(Path, paths):
        files += sorted(f for f in p.glob("*.json") if f.name != "_run.json") if p.is_dir() else [p]
    out = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        if shops and data["shop"] not in shops:
            continue
        if "state" not in data:
            raise SystemExit(f"{f}: no saved state (written by an older runner?)")
        out.append((data["split"], data["shop"], load_state(data["state"])))
    return out


def _summary(state: dict) -> dict:
    return {"skipped_rows": {k: len(v) for k, v in state.get("skipped_rows", {}).items()},
            "subtotal_labels": state.get("subtotal_labels", {}),
            "line_tables":[label(t.file, t.sheet) for t in state.get("line_tables", [])],
            "columns": {c.schema_column: c.raw_column for c in state.get("columns", [])},
            "open_columns": state.get("open_columns", []), "column_problems": state.get("column_problems", []),
            "fields_to_fill": state.get("fields_to_fill", []), "snapshots": state.get("snapshots", []),
            "anchor_tables": state.get("anchor_tables", []), "attached": state.get("attached", []),
            "missing_columns": state.get("missing_columns", {}),
            "statuses": {s.raw: s.status for s in state.get("statuses", [])},
            "blank_status": state.get("blank_status"),
            "forward_fill_columns": state.get("forward_fill_columns", []),
            "return_lines": state.get("return_lines"), "notes": state.get("notes", []),
            "service_fee_names": state.get("service_fee_names", []),
            "non_revenue_names": state.get("non_revenue_names", []),
            "conversions": [c.kind for c in state.get("conversions", [])],
            "plan_problems": state.get("plan_problems", [])}


def _record(graph_name: str, split: str, shop: str, state: dict, new: list[dict], seconds: float,
            error: str | None, finished: bool) -> dict:
    usage = Counter()
    for r in new:
        usage.update(r.get("usage", {}))
    return {"shop": shop, "split": split, "graph": graph_name, "finished": finished, "seconds": round(seconds, 1),
            "error": error, "usage": dict(usage), "steps": [r["step"] for r in new], "summary": _summary(state),
            "state": dump_state(state)}


def _write(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _print_step(shop: str, record: dict) -> None:
    shown = {k: v for k, v in record.items() if k != "prompt"}
    if isinstance(shown.get("attempts"), list):  # step 2: a retry's feedback is a whole self-contained prompt now
        shown["attempts"] = [{k: v for k, v in a.items() if k != "feedback"} for a in shown["attempts"]]
    if isinstance(shown.get("llm_check"), dict):  # the review's own prompt is long too
        shown["llm_check"] = {k: v for k, v in shown["llm_check"].items() if k != "prompt"}
    if isinstance(shown.get("blank_call"), dict):  # step 5's blank call has its own prompt
        shown["blank_call"] = {k: v for k, v in shown["blank_call"].items() if k != "prompt"}
    if isinstance(shown.get("shared_recheck"), dict):  # step 2's shared-column recheck: one prompt per field
        sr = dict(shown["shared_recheck"])
        sr["columns"] = {c: {**cr, "fields": {f: {k: v for k, v in it.items() if k != "prompt"}
                                              for f, it in cr.get("fields", {}).items()}}
                         for c, cr in (sr.get("columns") or {}).items()}
        shown["shared_recheck"] = sr
    if isinstance(shown.get("open_fields"), dict):  # step 2's pass 2: one prompt per open field
        of = dict(shown["open_fields"])
        of["fields"] = {f: {k: v for k, v in item.items() if k != "prompt"}
                        for f, item in (of.get("fields") or {}).items()}
        shown["open_fields"] = of
    for key in ("snapshots", "tables", "groups"):  # steps 0 and 3: one prompt per table
        if isinstance(shown.get(key), list):
            shown[key] = [{k: v for k, v in s.items() if k != "prompt"} for s in shown[key]]
    body = json.dumps(shown, ensure_ascii=False, indent=1, default=str)
    with _print_lock:
        print(f"--- [{shop}] {record.get('step', '?')} ---\n{body}\n", flush=True)


def _summary_text(shop: str, record: dict, new: list[dict]) -> str:
    s, usage = record["summary"], Counter(record["usage"])
    lines = [f"## {shop}: {record['seconds']:.0f}s, tokens in {usage['input']:,} out {usage['output']:,}"]
    if record["error"]:
        lines.append("  ERROR " + record["error"].strip().splitlines()[-1])
    for r in new:
        if r["step"] == "filter_rows":
            lines += _filter_summary(r)
        if r["step"] == "choose_line_tables":
            if r.get("error"):
                lines.append(f"  step 1: NO CHOICE ({r['error'][:200]})")
            else:
                how = "only option" if r.get("skipped") else f"chosen of {len(r.get('options', []))}"
                lines.append(f"  step 1: {r.get('answer') or r['options'][0]} ({how}, "
                             f"{len(s['line_tables'])} table(s))")
        if r["step"] == "map_line_columns":
            if r.get("error"):
                lines.append(f"  step 2: NO MAPPING ({r['error'][:200]})")
                continue
            n = len(r.get("attempts", []))
            lines.append(f"  step 2 ({n} attempt{'s' if n != 1 else ''}): {len(s['columns'])} mapped, "
                         f"{len(s['open_columns'])} open, {len(s['column_problems'])} problem(s) left")
            for a in r.get("attempts", [])[:-1]:  # what each retry was about
                asked = a["fields"] if a["fields"] == "all" else ", ".join(a["fields"])
                why = "unreadable reply" if a.get("error") else f"{len(a.get('problems', []))} problem(s)"
                lines.append(f"     attempt {a['attempt']} (asked {asked}): {why} → retry")
            for a in r.get("attempts", []):  # code's amount repair / qty_base decision (column_mapping/amounts.py)
                if a.get("code_fixes"):
                    lines.append(f"     attempt {a['attempt']}: code fixed "
                                 + ", ".join(f"{f} → {c}" for f, c in a["code_fixes"].items()))
            review = r.get("llm_check") or {}
            if review.get("code_fixes"):
                lines.append("     llm_check: code fixed " + ", ".join(f"{f} → {c}" for f, c in
                                                                     review["code_fixes"].items()))
            if review.get("call_failed"):
                lines.append(f"     llm_check: CALL FAILED, mapping unchanged ({review['call_failed'][:200]})")
            elif review.get("skipped") or review.get("error"):
                lines.append(f"     llm_check: {review.get('skipped') or 'unreadable reply'}")
            elif review:
                fixes = review.get("fixes", [])
                lines.append(f"     llm_check: {len(fixes)} fix(es)" + ("" if fixes else ", no change"))
                for f in fixes:
                    how = {True: "kept", False: f"dropped ({f.get('problem')})", "no change": "same as before"}
                    reason = f": {f['reason']}" if f.get("reason") else ""  # older runs' fixes had a reason
                    lines.append(f"       {f['field']}: {f['from']} → {f['to']}, {how[f['kept']]}{reason}")
            for column, cr in ((r.get("shared_recheck") or {}).get("columns") or {}).items():
                lines.append(f"     shared column {column!r}: {cr.get('verdict')}")
                for f, it in cr.get("fields", {}).items():
                    what = it.get("holds") or f"FAILED ({(it.get('call_failed') or it.get('error') or '')[:120]})"
                    lines.append(f"       {f}: {what}: {it.get('reason', '')}")
            for f, item in ((r.get("open_fields") or {}).get("fields") or {}).items():  # pass 2
                if item.get("call_failed") or item.get("error"):
                    what = f"FAILED, stays open: {(item.get('call_failed') or item.get('error'))[:200]}"
                else:
                    what = f"{item.get('column')} [{item.get('verdict')}]: {item.get('reason', '')}"
                lines.append(f"     open field {f} (candidates: {', '.join(item['candidates'])}): {what}")
            lines += [f"     {c} ← {raw}" for c, raw in s["columns"].items()]
            lines.append(f"     open: {', '.join(s['open_columns']) or '-'}")
            lines += [f"     problem: {' | '.join(p.splitlines())}" for p in s["column_problems"]]  # two-line
        if r["step"] == "find_anchors":
            lines += _anchors_summary(r)
        if r["step"] == "attach":
            lines += _attach_summary(r)
        if r["step"] == "resolve_anchors":
            lines += _resolve_summary(r)
        if r["step"] == "snapshot":
            lines += _snapshot_summary(r)
        if r["step"] == "report_missing":
            lines += _missing_summary(r)
        if r["step"] == "map_statuses":
            lines += _statuses_summary(r)
        if r["step"] == "cleaning_inputs":
            lines += _cleaning_summary(r)
        if r["step"] == "map_line_kinds":
            lines += _line_kinds_summary(r)
        if r["step"] == "map_conversions":
            lines += _conversions_summary(r)
        if r["step"] == "write_plan":
            lines += _write_plan_summary(r)
    return "\n".join(lines)


def _conversions_summary(r: dict) -> list[str]:
    """map_conversions: the README list (code), else the per-line answers (yes lines and failed calls shown with
    their reason), then the README rows, the table choice and its columns."""
    lines = [f"  conversions: sources {r.get('sources', [])}"]
    if r.get("readme_list"):
        lines.append(f"     README list: {r['readme_list']} rows (code)")
        return lines
    asked = (r.get("readme_writes") or {}).get("lines") or []
    yes = [a for a in asked if a.get("writes") == "yes"]
    failed = [a for a in asked if a.get("writes") is None]
    lines.append(f"     no README list; last section asked line by line: {len(asked)} lines, {len(yes)} yes, "
                 f"{len(failed)} no answer")
    for a in yes:
        lines.append(f"       YES {a['line'][:110]!r} - {a.get('reason')}")
    for a in failed:
        lines.append(f"       NO ANSWER {a['line'][:80]!r} ({a.get('call_failed') or a.get('error')})")
    rr = r.get("readme_rows") or {}
    if rr:
        rows = rr.get("rows") or []
        lines.append(f"     README rows ({len(rows)}, products on the lines: {rr.get('products_on_lines')}) - "
                     f"{rr.get('reason')}")
        lines += [f"       {x['product']}: 1 {x['unit']} = {x['factor']:g} {x.get('base_unit') or '?'}" for x in rows]
        lines += [f"     PROBLEM: {p}" for p in rr.get("problems", [])]
    c = r.get("table_choice") or {}
    if c.get("skipped"):
        lines.append(f"     conversion table: none asked ({c['skipped']})")
    elif c.get("call_failed") or c.get("error"):
        lines.append(f"     conversion table: NO ANSWER ({c.get('call_failed') or c.get('error')})")
    elif c:
        lines.append(f"     conversion table ({len(c.get('options', []))} options): {c.get('option')} - {c.get('reason')}")
    m = r.get("table_columns") or {}
    if m:
        lines.append(f"     columns: product {m.get('product_column')!r}, unit {m.get('unit_column')!r}, factor "
                     f"{m.get('factor_column')!r}, base unit {m.get('base_unit_column')!r} - {m.get('reason')}")
        match = ", ".join(f"{k} {v['coverage']:.0%}" for k, v in (m.get("key_match") or {}).items())
        lines.append(f"     code: factor_format {m.get('factor_format')}, key {m.get('key')} (products matched: {match})")
        if m.get("base_unit_dropped"):
            lines.append(f"     base unit {m['base_unit_dropped']!r} dropped (another role's column, or numbers)")
        lines += [f"     PROBLEM: {p}" for p in m.get("problems", [])]
        if m.get("warning"):
            lines.append(f"   WARNING: {m['warning']}")
    return lines


def _write_plan_summary(r: dict) -> list[str]:
    """write_plan: the plan's size, the choices code made (alternatives, extra links, warnings), the unmapped
    columns, then plan_check's problems."""
    if r.get("error"):
        return [f"  plan: NONE ({r['error']})"]
    probs = r.get("problems", [])
    lines = [f"  plan: {r['columns']} column(s), {r['joins']} join(s), conversions = {r['conversions']}; "
             f"plan_check: {'PASSED' if not probs else f'{len(probs)} problem(s)'}"]
    lines += [f"     PROBLEM: {p}" for p in probs]
    lines += [f"     {f} also filled by: {', '.join(alt)} (first kept)" for f, alt in r.get("alternatives", {}).items()]
    lines += [f"     link not used as a join: {x}" for x in r.get("extra_links", [])]
    lines += [f"   WARNING: {w}" for w in r.get("warnings", [])]
    un = r.get("unmapped", [])
    lines.append(f"     unmapped: {len(un)}")
    lines += [f"       {u}" for u in un]
    return lines


def _line_kinds_summary(r: dict) -> list[str]:
    """map_line_kinds: the candidates (why each was picked, share, unusual lines) with the answer; the non-items
    first, then the items on one line."""
    if r.get("skipped"):
        return [f"  line kinds: {r['skipped']}"]
    # final = after pass 2 (states saved before pass 2 have only answers)
    cands, answers = r.get("candidates", []), r.get("final") or r.get("answers") or {}
    first, recheck = r.get("answers") or {}, r.get("recheck") or {}
    lines = [f"  line kinds: {len(cands)} candidate(s) of {r['keys']:,} products (by {r['key_col']!r}, names from "
             f"{r['name_source']})"]
    if r.get("call_failed") or r.get("error"):
        lines.append(f"     {'CALL FAILED' if r.get('call_failed') else 'UNREADABLE REPLY'}, both lists empty: "
                     f"{(r.get('call_failed') or r.get('error'))[:200]}")
    items = []
    for c in cands:
        kind = answers.get(c["pattern"])
        if kind == "item":
            items.append(c["pattern"])
            continue
        names = "" if c["names"] == [c["pattern"]] else f" = {', '.join(c['names'][:5])}"
        lines.append(f"     {(kind or '?').upper()}: {c['pattern']!r}{names} ({c['lines']:,} lines, {c['share']:.0%} "
                     f"of orders, {c['unusual']:.0%} unusual; {' + '.join(c['why'])})")
    for c in cands:  # pass 2: every name asked again alone, with its reason (also those that became items)
        rec = recheck.get(c["pattern"])
        if rec is None:
            continue
        what = f"{first.get(c['pattern'])} -> {rec['kind']}: {rec.get('reason', '')}" if rec.get("kind") else \
            f"FAILED, pass 1 kept: {(rec.get('call_failed') or rec.get('error') or '')[:200]}"
        lines.append(f"       recheck {c['pattern']!r}: {what}")
    if items:
        lines.append(f"     items: {', '.join(items)}")
    return lines


def _cleaning_summary(r: dict) -> list[str]:
    """cleaning_inputs: the return lines (count, examples, warnings), then forward fill's columns (each with its
    fields and blank continuation lines), the columns left out, the orders differing only on return lines, the
    warnings."""
    rl = r.get("return_lines", {})
    if rl.get("skipped"):
        lines = [f"  return lines: no ({rl['skipped']})"]
    else:
        lines = [f"  return lines: {'yes' if rl['return_lines'] else 'no'}, {rl['count']:,} line(s) with a negative "
                 f"{rl['qty_col']!r}"]
        lines += [f"     e.g. {e}" for e in rl.get("examples", [])]
    lines += [f"   WARNING: {p}" for p in rl.get("problems", [])]
    dup = r.get("duplicates", {})
    if dup.get("skipped"):
        lines.append(f"  duplicates: none counted ({dup['skipped']})")
    elif dup:
        skip = f" ({dup['not_revenue_rows']:,} not-revenue row(s) left out)" if dup.get("not_revenue_rows") else ""
        lines.append(f"  duplicates: {dup['count']:,} line(s) in {dup['orders']:,} order(s){skip}")
        lines += [f"     e.g. {e}" for e in dup.get("examples", [])]
    ff = r.get("forward_fill", {})
    if ff.get("skipped"):
        return lines + [f"  forward fill: none ({ff['skipped']})"]
    lines.append(f"  forward fill: {ff['continuation']:,} of {ff['rows']:,} line rows continue an order "
                 f"(blank {ff['order_col']!r}); {len(ff['columns'])} column(s)")
    for c in ff["columns"]:
        if c == ff["order_col"]:
            lines.append(f"     fill {c!r} (order id)")
        else:
            lines.append(f"     fill {c!r} ({', '.join(ff['fields'][c])}): blank on "
                         f"{ff['blank_on_continuation'][c]:,} continuation row(s)")
    lines += [f"     not filled {c!r} ({', '.join(ff['fields'][c])}): {why}" for c, why in ff["left_out"].items()]
    lines += [f"     {c!r}: {n:,} order(s) differ only on return lines (expected: the return's own date)"
              for c, n in ff.get("return_only", {}).items()]
    lines += [f"   WARNING: {p}" for p in ff.get("problems", [])]
    return lines


def _answer_key(word: str) -> str:
    """parse.map_status as the answer key only (the workflow never uses it): its status, or '?' for a word the
    hand-written list does not know."""
    from src.datamate.parse import map_status
    try:
        return map_status(word) or "?"
    except ValueError:
        return "?"


def _statuses_summary(r: dict) -> list[str]:
    """map_statuses: the column, then each word with its count, its status and where it came from: 'known' (the
    known-word list) or 'LLM' marked against the answer key (✓ same, ✗ different, ? the key does not know it)."""
    if r.get("skipped") and "column" not in r:
        bc = r.get("blank_call") or {}  # 5e: the no-column call (2026-11-13)
        tail = (f"; no-column call -> blank {bc.get('blank')} ({bc.get('reason') or bc.get('call_failed') or 'no reason'})"
                if bc else "")
        return [f"  map_statuses: {r['skipped']}{tail}"]
    known, asked = r.get("known", {}), r.get("asked", [])
    head = (f"  map_statuses: '{r['column']}' of [{r['table']}] ({r['source']}), {len(r['words'])} word(s): "
            f"{len(known)} known, {len(asked)} asked{'' if r.get('known_words', True) else ' (--no-known-words)'}; "
            f"blank on {r['blank']:,} of {r['rows']:,} rows")
    lines = [head]
    if r.get("skipped"):
        return lines + [f"     {r['skipped']}"]
    if r.get("call_failed") or r.get("error"):
        lines.append(f"     {'CALL FAILED' if r.get('call_failed') else 'UNREADABLE REPLY'}, statuses left empty: "
                     f"{(r.get('call_failed') or r.get('error'))[:200]}")
    wrong = 0
    for w, n in r["words"].items():
        got = r["statuses"].get(w)
        if w in known:
            lines.append(f"     known {w!r} ×{n:,} → {known[w]}")
            continue
        key = _answer_key(w)
        mark = "?" if key == "?" or got is None else ("✓" if got == key else "✗")
        wrong += mark == "✗"
        lines.append(f"     LLM {mark} {w!r} ×{n:,} → {got or '-'}" + (f"  (key: {key})" if mark == "✗" else ""))
    if asked:
        lines.append(f"   {wrong} of {len(asked)} asked word(s) differ from the answer key")
    if r.get("leftover_blanks"):  # blank on rows whose order id is filled: not explained by forward fill
        lines.append(f"   leftover blanks: {r['leftover_blanks']:,} (order id {r.get('order_col')!r}) → "
                     f"{r.get('blank_status') or '-'}")
        bc = r.get("blank_call") or {}  # the blank's own call (2026-11-11): its reason, or why it has none
        if bc.get("reason"):
            lines.append(f"     reason: {bc['reason']}")
        elif bc.get("call_failed") or bc.get("error"):
            lines.append(f"     BLANK CALL FAILED: {(bc.get('call_failed') or bc.get('error'))[:200]}")
    return lines


def _filter_summary(r: dict) -> list[str]:
    """filter_rows: per table group its repeated headers, candidates and each pattern's verdict, the regexes and
    their problems. Groups without anything to report are counted, not listed."""
    quiet = [g for g in r["groups"] if not g["repeated_headers"] and not g["candidates"]]
    lines = [f"  filter_rows: {len(r['groups'])} table group(s), {r['calls']} call(s), "
             f"{len(quiet)} with nothing to filter"]
    for g in r["groups"]:
        if g in quiet:
            continue
        lines.append(f"     [{g['table']}] {g['data_rows']:,} rows, {g['repeated_headers']} repeated header(s), "
                     f"{g['candidates']} candidate(s), {len(g['dense_columns'])} dense column(s); "
                     f"{g['skipped']:,} skipped")
        if g.get("error"):
            lines.append(f"        UNREADABLE REPLY, no pattern marked: {g['error'][:150]}")
        for p in g.get("patterns", []):
            mark = {"summary or note row": "SKIP", "data row": "keep", None: "?   "}[p["answer"]]
            lines.append(f"        {mark} group {p['group']}: {p['key'][:80]} ({p['rows']} rows)")
        lines += [f"        regex {rx}" for rx in g["regexes"]]
        lines += [f"        PROBLEM: {p}" for p in g.get("problems", [])]
    return lines


def _missing_summary(r: dict) -> list[str]:
    """report_missing: each missing field with its reason (known gaps marked), then the problems."""
    missing, gaps = r["missing_columns"], r.get("known_gaps", {})
    lines = [f"  report_missing: {len(missing)} missing"]
    for f, why in missing.items():
        lines.append(f"     {f}: {why}" + ("  (known gap)" if f in gaps else ""))
    lines += [f"   PROBLEM: {p}" for p in r.get("problems", [])]
    return lines


def _snapshot_summary(r: dict) -> list[str]:
    """snapshot: per attached table its anchors, the fields asked and the answers; the skipped tables; then per
    field every table that claimed it (✓ passed its check, ✗ failed), conflicts flagged, and the unreached fields."""
    if r.get("error") or r.get("skipped"):
        return [f"  snapshot: {r.get('error') or r.get('skipped')}"]
    lines = [f"  snapshot: {len(r['fields'])} field(s) to fill ({', '.join(r['fields'])}), "
             f"{len(r['snapshots'])} table(s) asked, {len(r['skipped_tables'])} skipped"]
    for snap in r["snapshots"]:
        head = f"     [{snap['table']}] round {snap['round']}, by {', '.join(snap['anchors'])}"
        if snap.get("call_failed") or snap.get("error"):
            lines.append(f"{head}: {'CALL FAILED' if snap.get('call_failed') else 'unreadable reply'}: "
                         f"{(snap.get('call_failed') or snap.get('error'))[:150]}")
            continue
        got = [f"{f} ← {c}" for f, c in snap["filled"].items()] + \
              [f"{f} ✗ ({snap['answer'][f]})" for f in snap["failed"]]
        lines.append(f"{head}; asked {', '.join(snap['asked'])}")
        lines.append(f"        {', '.join(got) or 'nothing'}")
        if snap.get("retry"):  # the shared-column retry (2026-11-11)
            rt = snap["retry"]
            first = ", ".join(f"{f} ← {rt['first_answer'][f]}" for f in rt["fields"])
            again = ("CALL FAILED" if rt.get("call_failed") else
                     ", ".join(f"{f} ← {c}" for f, c in rt["answer"].items()) if rt.get("answer") else
                     "unreadable reply")
            lines.append(f"        shared-column retry: {first}  →  {again}")
        for p in snap.get("shared_left", []):
            lines.append(f"        still shared: {p.splitlines()[0]}")
    for t in r["skipped_tables"]:
        lines.append(f"     [{t['table']}] skipped (by {', '.join(t['anchors'])}): {t['why']}")
    lines.append("   by field:")
    for f, claims in r["by_field"].items():
        shown = "; ".join(f"{c['table']} ← {c['column']} {'✓' if c['passed'] else '✗'}" for c in claims)
        flag = "  ← CONFLICT" if len({(c['table'], c['column']) for c in claims if c['passed']}) > 1 else ""
        lines.append(f"     {f}: {shown or '(no table)'}{flag}")
    lines.append(f"   unreached (no table attached by their anchor): {', '.join(r['unreached']) or '-'}")
    return lines


def _resolve_summary(r: dict) -> list[str]:
    """resolve_anchors: each anchor's candidates and the chosen table, then the attach result after the drops."""
    if r.get("skipped"):
        return [f"  resolve_anchors: {r['skipped']}"]
    lines = [f"  resolve_anchors: {len(r['choices'])} anchor(s) with several tables"]
    for c in r["choices"]:
        cands = ", ".join(f"{x['table']} '{x['column']}'" for x in c["candidates"])
        if c.get("call_failed") or not c["chosen"]:
            what = "CALL FAILED" if c.get("call_failed") else "unreadable reply"
            lines.append(f"     {c['anchor']}: {what}, nothing dropped (candidates: {cands})")
        else:
            lines.append(f"     {c['anchor']}: chose {c['chosen']}; dropped {', '.join(c['dropped'])}")
    return lines + ["   after the drops:"] + _attach_summary(r)[1:]


def _attach_summary(r: dict) -> list[str]:
    """attach: each attached table by round with its links, then every table left out with why."""
    if r.get("error"):
        return [f"  attach: {r['error']}"]
    rule = f", unique ≥ {r['min_unique']:.0%}" if r.get("min_unique") else ""
    lines = [f"  {r['step']}: {len(r['attached']) - 1} table(s) attached, {len(r['left_out'])} left out{rule}"]
    for a in r["attached"]:
        if a["round"] == 0:
            lines.append(f"     round 0 [{a['table']}] (the lines)")
            continue
        links = "; ".join(f"{k['anchor']}: '{k['column']}'"
                          f"{' (together with ' + repr(k['pair_with']) + ')' if k.get('pair_with') else ''}"
                          f" ↔ [{k['to_table']}] '{k['to_column']}' "
                          f"(covers {k['coverage']:.0%}, unique {k['unique']:.1%})" for k in a["links"])
        lines.append(f"     round {a['round']} [{a['table']}] {links}")
    lines.append("   left out:")
    for t in r["left_out"]:
        why = "; ".join(f"{c['anchor']} '{c['column']}': {c['why']}" + (
            f" (unique {c['unique']:.1%}, best cover {c['best_coverage']:.0%}"
            f"{' of ' + c['best_with'] if c['best_with'] else ''})" if "unique" in c else "")
            for c in t["claims"]) or "no anchors claimed"
        lines.append(f"     [{t['table']}] {why}")
    return lines


def _anchors_summary(r: dict) -> list[str]:
    """find_anchors: each table's claimed anchors, then per anchor every table that claimed it (the line first)."""
    if r.get("error") or r.get("skipped"):
        return [f"  find_anchors: {r.get('error') or r.get('skipped')}"]
    calls = sum(not t["line"] for t in r["tables"])
    lines = [f"  find_anchors: {len(r['tables'])} table(s), {calls} call(s)"]
    for t in r["tables"]:
        if t.get("call_failed") or t.get("error"):
            lines.append(f"     [{t['table']}] {'CALL FAILED' if t.get('call_failed') else 'unreadable reply'}: "
                         f"{(t.get('call_failed') or t.get('error'))[:150]}")
            continue
        got = ", ".join(f"{a} ← {c}" for a, c in t["anchors"].items()) or "no anchors"
        lines.append(f"     [{t['table']}]{' (line, step 2)' if t['line'] else ''} {got}")
    lines.append("   by anchor:")
    for a, claims in r["by_anchor"].items():
        shown = "; ".join(f"{c['table']}{' (line)' if c['line'] else ''} ← {c['column']}" for c in claims)
        lines.append(f"     {a}: {shown or '(no table)'}")
    return lines


def run_shop(graph, graph_name: str, split: str, shop: str, state: dict, context: ExploreContext,
             out_dir: Path) -> str:
    start = time.perf_counter()
    path = out_dir / f"{shop}.json"
    before = len(state.get("trace", []))  # trace records from earlier runs: not printed, not counted again
    last, error = dict(state), None
    try:
        # stream(stream_mode="values") yields the whole state after every step, so a crash keeps what was reached
        printed = before
        for values in graph.stream(state, context=context, stream_mode="values"):
            last = values
            trace = last.get("trace", [])
            _write(path, _record(graph_name, split, shop, last, trace[before:], time.perf_counter() - start,
                                 None, finished=False))
            for r in trace[printed:]:
                _print_step(shop, r)
            printed = len(trace)
    except Exception:  # noqa: BLE001 - recorded in the output file, the other shops go on
        error = traceback.format_exc()
    new = last.get("trace", [])[before:]
    record = _record(graph_name, split, shop, last, new, time.perf_counter() - start, error, finished=True)
    _write(path, record)
    return _summary_text(shop, record, new)


def run_all(graph, graph_name: str, jobs: list[tuple[str, str, dict]], model, workers: int, out_dir: Path) -> None:
    """jobs: (split, shop, input state). Shops run in parallel with `workers`."""
    print(f"{len(jobs)} shop(s), graph {graph_name} → {out_dir}\n")
    summaries = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_shop, graph, graph_name, split, shop, state, context_for(model, split), out_dir)
                   for split, shop, state in jobs]
        for f in as_completed(futures):
            summary = f.result()
            summaries.append(summary)
            with _print_lock:
                print(summary, flush=True)
    # the console summaries also in a file, so a closed console loses nothing (show_run_v2.py rebuilds them too)
    (out_dir / "_summary.txt").write_text("\n\n".join(sorted(summaries)) + "\n", encoding="utf-8")


def summary_from_file(path: Path) -> str:
    """The console summary of one saved shop file, rebuilt from its trace: the last len(steps) records are the ones
    that run added."""
    record = json.loads(path.read_text(encoding="utf-8"))
    trace = record["state"].get("trace", [])
    new = trace[len(trace) - len(record["steps"]):] if record["steps"] else []
    return _summary_text(record["shop"], record, new)
