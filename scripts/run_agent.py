"""Run the query_agent on the questions of converted shops and score it against the answer key.

Loads .env (API key). Writes to --out-dir: submission.jsonl (one line per question, as it finishes) and
traces.jsonl (every message of each run: tool calls, tool outputs, final reply, plus timing: when the question
started after the run began, its seconds, each graph step's seconds, and token usage per LLM reply; the finalize
call's tokens are not in the messages, only its time), then prints a summary.

Usage:
  .venv/Scripts/python.exe scripts/run_agent.py --sample 20                 # 20 random questions of the 5 shops
  .venv/Scripts/python.exe scripts/run_agent.py --shops biz_108 --limit 10
  .venv/Scripts/python.exe scripts/run_agent.py --ids q_001883280e83 q_396a12d45bb0
  .venv/Scripts/python.exe scripts/run_agent.py --workers 8                 # all questions of the 5 shops
"""
import argparse
import datetime as dt
import json
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252, which cannot print Vietnamese

from dotenv import load_dotenv  # noqa: E402
from langchain_anthropic import ChatAnthropic  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402

from src.datamate.query_agent.agent import QueryContext, query_agent  # noqa: E402
from src.datamate.grading import is_correct  # noqa: E402

load_dotenv()

REFERENCE_SHOPS = {  # shops with a converted table in data_std/<split>/ (scripts/convert_reference.py)
    "train": ["biz_108", "biz_116", "biz_117", "biz_122", "biz_128"],
    "public_test": [f"dev_{i:03d}" for i in range(1, 15)],
}
# Each agent → tools round trip is 2 steps: about 6 tool turns, then finalize. The target is 1–3 turns.
RECURSION_LIMIT = 14


def _message_usage(m) -> Counter:
    meta = getattr(m, "usage_metadata", None) or {}
    details = meta.get("input_token_details") or {}
    # langchain-anthropic reports cache writes under the TTL keys and zeroes the generic cache_creation then
    write = sum(details.get(k) or 0 for k in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return Counter({"input": meta.get("input_tokens", 0), "output": meta.get("output_tokens", 0),
                    "cache_read": details.get("cache_read") or 0, "cache_write": write})


def _trace(messages) -> list[dict]:
    out = []
    for m in messages:
        if isinstance(m, AIMessage):
            out.append({"role": "ai", "content": m.content if isinstance(m.content, str) else
                        " ".join(b.get("text", "") for b in m.content if isinstance(b, dict)),
                        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in m.tool_calls],
                        "usage": dict(_message_usage(m))})
        elif isinstance(m, ToolMessage):
            out.append({"role": "tool", "name": m.name, "content": m.content})
    return out


def _usage(messages) -> Counter:
    u = Counter()
    for m in messages:
        u.update(_message_usage(m))
    return u


def run_one(q: dict, split: str, context: QueryContext, run_start: float) -> dict:
    start = time.perf_counter()
    state = {"question": q["question"], "as_of_date": q["as_of_date"],
             "shop_dir": str(ROOT / "data_std" / split / q["business_id"]),
             "calendar_path": str(ROOT / "dataset" / "zaloAI" / split / "calendar.csv"),
             "messages": [], "results": {}}
    # stream() instead of invoke(): it yields after every step, so the state reached so far survives a
    # GraphRecursionError (its messages go into the trace), and the time between yields is each step's duration.
    # stream_mode=["updates", "values"] yields (mode, chunk) pairs: "updates" names the node that just ran,
    # "values" is the whole state after that step.
    last, steps, error, mark = state, [], None, start
    try:
        for mode, chunk in query_agent.stream(state, context=context, stream_mode=["updates", "values"],
                                                 config={"recursion_limit": RECURSION_LIMIT}):
            if mode == "values":
                last = chunk
            else:
                now = time.perf_counter()
                steps += [{"node": node, "seconds": round(now - mark, 2)} for node in chunk]
                mark = now
        submission = last["submission"]
    except GraphRecursionError:
        submission, error = {"type": "abstain"}, "recursion limit"  # out of steps: abstain (0), never guess (−1)
    except Exception as e:  # noqa: BLE001 - one failing question must not stop the run
        submission, error = {"type": "abstain"}, f"{type(e).__name__}: {e}"
    messages = last.get("messages", [])
    return {"q": q, "submission": submission, "started": round(start - run_start, 2),
            "seconds": time.perf_counter() - start, "error": error,
            "tool_turns": sum(1 for m in messages if isinstance(m, AIMessage) and m.tool_calls),
            "usage": _usage(messages), "steps": steps, "trace": _trace(messages)}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--split", default="train")
    p.add_argument("--shops", nargs="*", help="default: every converted shop of the split")
    p.add_argument("--ids", nargs="*", help="only these question ids")
    p.add_argument("--sample", type=int, help="N random questions (with --seed)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, help="only the first N questions")
    p.add_argument("--workers", type=int, default=1, help="questions run in parallel")
    p.add_argument("--model", default="claude-sonnet-5-5")
    p.add_argument("--out-dir", default=None, help="default: runs/<timestamp>")
    args = p.parse_args()

    split_dir = ROOT / "dataset" / "zaloAI" / args.split
    questions = [json.loads(l) for l in open(split_dir / "questions.jsonl", encoding="utf-8")]
    questions = [q for q in questions if q["business_id"] in set(args.shops or REFERENCE_SHOPS[args.split])]
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids)]
    if args.sample:
        questions = random.Random(args.seed).sample(questions, min(args.sample, len(questions)))
    if args.limit:
        questions = questions[: args.limit]
    answers_path = split_dir / "answers.jsonl"
    answers = {a["id"]: a for a in map(json.loads, open(answers_path, encoding="utf-8"))} if answers_path.is_file() else {}

    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "runs" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    context = QueryContext(model=ChatAnthropic(model=args.model), rulebook=(split_dir / "rulebook.md").read_text(encoding="utf-8"))
    print(f"{len(questions)} questions, {args.workers} workers, model {args.model} → {out_dir}\n")

    lock, stats, by_type = threading.Lock(), Counter(), {}
    usage, seconds, turns = Counter(), [], []
    with open(out_dir / "submission.jsonl", "w", encoding="utf-8") as sub, \
            open(out_dir / "traces.jsonl", "w", encoding="utf-8") as tr, ThreadPoolExecutor(args.workers) as pool:
        run_start = time.perf_counter()
        futures = [pool.submit(run_one, q, args.split, context, run_start) for q in questions]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            q, s = r["q"], r["submission"]
            key = answers.get(q["id"])
            if s["type"] == "abstain":
                verdict = "ABSTAIN"
            elif key is None:
                verdict = ""
            else:
                verdict = "OK" if is_correct(s, key) else "BAD"
            with lock:
                sub.write(json.dumps({"id": q["id"], **s}, ensure_ascii=False) + "\n")
                sub.flush()  # keep finished answers if the run is stopped midway
                tr.write(json.dumps({"id": q["id"], "shop": q["business_id"], "question": q["question"],
                                     "as_of_date": q["as_of_date"], "verdict": verdict, "submission": s,
                                     "expected": key, "error": r["error"], "started": r["started"],
                                     "seconds": round(r["seconds"], 2), "tool_turns": r["tool_turns"],
                                     "usage": dict(r["usage"]), "steps": r["steps"], "trace": r["trace"]},
                                    ensure_ascii=False) + "\n")
                tr.flush()
                stats[verdict] += 1
                if key:
                    by_type.setdefault(key["type"], Counter())[verdict] += 1
                usage.update(r["usage"])
                seconds.append(r["seconds"])
                turns.append(r["tool_turns"])
            exp = f"  expected {json.dumps(key.get('answer', key['type']), ensure_ascii=False)}" if verdict == "BAD" else ""
            err = f"  [{r['error']}]" if r["error"] else ""
            print(f"[{i}/{len(questions)}] {verdict:7} {q['business_id']} {q['id']} {r['seconds']:.0f}s {r['tool_turns']} turns{err}")
            print(f"    Q: {q['question']}\n    A: {json.dumps(s, ensure_ascii=False)}{exp}")

    n = len(questions)
    if not n:
        return
    print(f"\n== {n} questions: OK {stats['OK']}, BAD {stats['BAD']}, ABSTAIN {stats['ABSTAIN']}"
          f"  → score {stats['OK'] - stats['BAD']} (+1 / −1 / 0)")
    print(f"   precision when answering: {stats['OK'] / max(1, stats['OK'] + stats['BAD']):.1%}")
    for t, c in sorted(by_type.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"   {t:13} OK {c['OK']:3}  BAD {c['BAD']:3}  ABSTAIN {c['ABSTAIN']:3}")
    print(f"   avg {sum(seconds) / n:.1f}s and {sum(turns) / n:.1f} tool turns per question; agent tokens: "
          f"{usage['input']:,} in ({usage['cache_read']:,} from cache, {usage['cache_write']:,} written to cache), "
          f"{usage['output']:,} out")
    print(f"   traces: {out_dir / 'traces.jsonl'}")


if __name__ == "__main__":
    main()
