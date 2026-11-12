"""The query_agent (answers questions): agent ⇄ tools → finalize.

The agent node lets the LLM call find_entity / information_finder / calculate until it replies without tool
calls; finalize turns that reply into a §9 submission with structured output.
"""
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.datamate.query_agent.prompts import ANALYSIS, ANALYZE, FINALIZE, INSIGHT, RECHECK_TYPE, ROUTE, SYNTHESIZE, SYSTEM, TASK_HINT, USER, VERIFY, VERIFY_RETRY, QUARTER_CHECK, ROUTE_OFF, AGENT_OFF
from src.datamate.query_agent.periods import resolve_period
from src.datamate.query_agent.state import QueryState
from src.datamate.query_agent.tools import TOOLS, Period, _lunar, _shop
from src.datamate.query_agent.task_tools import TASK_TOOLS, answer_from

OUT_OF_SCOPE = "OUT OF SCOPE"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# What the agent can and cannot ask of a shop, by the column it needs
FEATURES = {
    "order_id": "orders (order counts, cancellations, per-order questions)",
    "product_key": "products (product names, groups, quantities sold)",
    "customer_key": "customers", "customer_group": "customer groups", "product_group": "product groups",
    "channel": "sales channel", "payment_method": "payment method", "seller": "seller",
    "order_time": "order time (needed for shifts)",
    "cost_price": "cost price on sales lines (needed for lãi gộp)",
}


@dataclass
class QueryContext:
    """Fixed for the whole run and passed at invoke time; nodes read it via runtime.context."""

    model: BaseChatModel
    rulebook: str
    cache_control: bool = True  # Anthropic prompt caching; False for OpenAI-compatible endpoints (vLLM)
    # vLLM + Qwen3.5: guided json_schema gives garbage JSON (seen 2026-11-10). True: finalize asks for the JSON in
    # plain text (thinking off, then on as fallback); the agent node keeps thinking.
    finalize_no_think: bool = False
    # True: an analyze step (LLM) first rewrites the question and plans the calls; the agent and finalize see it
    analyze: bool = False
    # > 0 (with analyze): the analyze step gives up to this many readings of the question, each runs its own
    # agent ⇄ tools loop in parallel, and a synthesize step picks one (or abstains when none fits)
    branches: int = 0
    # True (with analyze): the analyze step routes the question to a task family; the agent gets that family's task
    # tool (one call → final answer) and finalize takes the answer straight from it
    router: bool = False
    # Thinking of the router call: "on" (thinking, then without on a parse failure), "capped" (thinking with at most
    # think_cap output tokens, then without), "off" (without, then capped thinking on a parse failure), "budget"
    # (think at most think_cap tokens, then close the thinking and write the reply; see _budget_reply), "prompt"
    # (thinking off; its own prompt ROUTE_OFF: the reasoning is written step by step in the JSON)
    router_think: str = "on"
    think_cap: int = 2048
    # True (with router): a short check (thinking off) that the task tool's call matches the question; on a mismatch
    # the agent calls the tool again once with the problem in view, a second mismatch → abstain
    verify: bool = False
    # True (with verify): also check the general path's information_finder / calculate calls before finalize
    verify_general: bool = False
    # False: the agent node (tool calls) runs with Qwen thinking off too
    agent_think: bool = True


def shop_brief(shop_dir: str) -> str:
    shop = _shop(shop_dir)
    m = shop.manifest
    lines = [f"- data from {m['date_min']} to {m['data_end']}"]
    missing = [label for col, label in FEATURES.items() if not shop.has(col)]
    lines.append("- not recorded (questions needing these are unanswerable): " + (", ".join(missing) or "nothing"))
    for col, label in (("product_group", "product groups"), ("customer_group", "customer groups")):
        if shop.has(col):
            lines.append(f"- {label}: " + ", ".join(sorted(shop.lines[col].dropna().unique())))
    lines += [f"- {note}" for note in m.get("brief", [])]  # how this shop's records map onto sales lines
    return "\n".join(lines)


def _opening_messages(state: QueryState, context: QueryContext) -> list:
    """System prompt (cached: identical for every question) and the question; rebuilt on each call."""
    as_of = dt.date.fromisoformat(state["as_of_date"])
    if context.cache_control:
        system = SystemMessage(content=[{"type": "text", "text": SYSTEM.format(rulebook=context.rulebook),
                                         "cache_control": {"type": "ephemeral"}}])
    else:  # vLLM: plain string system prompt; its automatic prefix caching covers the shared prefix
        system = SystemMessage(content=SYSTEM.format(rulebook=context.rulebook))
    user = USER.format(question=state["question"], as_of_date=state["as_of_date"],
                       weekday=WEEKDAYS[as_of.weekday()], shop_brief=shop_brief(state["shop_dir"]))
    if state.get("analysis"):
        user += ANALYSIS.format(analysis=state["analysis"])
    task_tool = TASK_TOOLS.get(state.get("task") or "")
    if task_tool is not None:
        user += TASK_HINT.format(tool=task_tool.name)
    return [system, HumanMessage(user)]


_PERIOD_FIELDS = ("date", "month", "quarter", "year", "n", "from_date", "to_date")
_FILTER_FIELDS = ("product_keys", "product_group", "customer_keys", "customer_group", "channel", "payment_method",
                  "seller", "shift", "size", "base_unit")


def _int_or_keep(v):
    return int(v) if isinstance(v, str) and v.strip().lstrip("-").isdigit() else v


def repair_tool_calls(response):
    """Step before the tools run: small models (Qwen3.5-9B) often write information_finder's nested `period` flat
    ("period": "month", "month": 5) or as JSON text. Rewrite such calls into the tool's own schema (unchanged) so a
    format slip does not burn a turn. Calls already in the right shape pass through untouched."""
    for call in response.tool_calls:
        if call["name"] != "information_finder":
            continue
        args = call["args"]
        for key in ("period", "filters"):  # nested object sent as JSON text
            if isinstance(args.get(key), str) and args[key].strip().startswith("{"):
                try:
                    args[key] = json.loads(args[key])
                except json.JSONDecodeError:
                    pass
        period = args.get("period")
        flat = {k: args.pop(k) for k in _PERIOD_FIELDS if k in args}
        if isinstance(period, str):
            if "," in period and not flat:  # "2025-11-01,2025-11-30"
                a, b = (x.strip() for x in period.split(",", 1))
                period = {"kind": "range", "from_date": a, "to_date": b}
            else:
                period = {"kind": period}
        if isinstance(period, dict):
            period = {**flat, **period}
            for k in ("month", "quarter", "year", "n"):
                if k in period:
                    period[k] = _int_or_keep(period[k])
            args["period"] = period
        elif flat:
            args.update(flat)  # nothing to attach to: leave it for the tool's error message
        loose = {k: args.pop(k) for k in _FILTER_FIELDS if k in args}  # filter fields written next to filters
        if loose:
            args["filters"] = {**loose, **(args.get("filters") if isinstance(args.get("filters"), dict) else {})}
        f = args.get("filters")
        if isinstance(f, dict):
            for k in ("product_keys", "customer_keys"):  # one key given as a bare string
                if isinstance(f.get(k), str):
                    f[k] = [f[k]]
    return response


_TOOL_NAMES = {t.name for t in TOOLS} | {t.name for t in TASK_TOOLS.values()}
# Families the router confuses with each other: the agent also gets the neighbour's tool and picks by the question
# ("khách có từ N đơn chiếm mấy %" routed to share is count_where's; "từng bên bao nhiêu" routed to compare is a list)
_SIBLINGS = {"share": ("count_threshold",), "compare": ("values_by_period",), "values_by_period": ("compare",)}


_TASK_NESTED = ("period", "against", "periods", "filters", "part", "whole")


def _router_period(state) -> dict | None:
    try:
        p = json.loads(state.get("analysis") or "{}").get("period")
    except json.JSONDecodeError:
        return None
    return p if isinstance(p, dict) else None


def _prefer_router_month(args: dict, routed: dict | None) -> None:
    """Two readings of the same period disagree on month vs quarter (T3/2025 read as quý 3): the router, which reads the
    question once with the date conventions in view, decides."""
    p = args.get("period")
    if not (isinstance(p, dict) and routed and p.get("kind") == "quarter" and routed.get("kind") == "month"):
        return
    if _int_or_keep(p.get("quarter")) == _int_or_keep(routed.get("month")) and \
            _int_or_keep(p.get("year")) == _int_or_keep(routed.get("year")):
        args["period"] = dict(routed)


def _prefer_router_period(args: dict, routed: dict, state) -> None:
    """One-value question, one call: the router read the period once with the date conventions in view; when the
    agent's own period covers other dates (it often recomputes 'tuần này' / 'tuần rồi' by hand), the router's wins."""
    p = args.get("period")
    if not isinstance(p, dict) or p == routed:
        return
    try:
        as_of, lunar = dt.date.fromisoformat(state["as_of_date"]), _lunar(state["calendar_path"])
        mine = resolve_period(Period.model_validate(p).spec(), as_of, lunar)
        theirs = resolve_period(Period.model_validate(routed).spec(), as_of, lunar)
    except Exception:  # noqa: BLE001 - an invalid period on either side: leave the call as it is
        return
    if (mine.start, mine.end) != (theirs.start, theirs.end):
        args["period"] = dict(routed)


def _router_answer_type(state) -> str:
    try:
        return str(json.loads(state.get("analysis") or "{}").get("answer_type", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        return ""


def repair_task_calls(response, state=None):
    """Same idea as repair_tool_calls for the task tools: nested arguments sent as JSON text are decoded."""
    routed = _router_period(state or {})
    for call in response.tool_calls:
        if call["name"] in ("information_finder", *(t.name for t in TASK_TOOLS.values())):
            _prefer_router_month(call["args"], routed)
        if call["name"] == "information_finder" and (state or {}).get("task") == "single_value" and routed and \
                sum(c["name"] == "information_finder" for c in response.tool_calls) == 1 and \
                not call["args"].get("compare"):
            _prefer_router_period(call["args"], routed, state)
        if call["name"] not in {t.name for t in TASK_TOOLS.values()}:
            continue
        if call["name"] == "unsold_products":  # a count unless the router also reads it as a list of names
            if call["args"].get("names") and _router_answer_type(state or {}) == "number":
                call["args"]["names"] = False
        for key in _TASK_NESTED:
            v = call["args"].get(key)
            if isinstance(v, str) and v.strip()[:1] in "{[":
                try:
                    call["args"][key] = json.loads(v)
                except json.JSONDecodeError:
                    pass
    return response


def _call_key(call) -> str:
    return call["name"] + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)


def conclude_if_done(response, messages):
    """Step after repair: small models often do not stop on their own. They invent an answer tool (final_answer,
    answer, submit...) or repeat a call whose output they already have. Both mean "I am done": drop the calls and
    go to finalize with the last tool outputs as the conclusion. A turn with any new valid call runs as usual."""
    if not response.tool_calls and str(response.content or "").strip():
        return response
    done_before = {_call_key(c) for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    invented = [c for c in response.tool_calls if c["name"] not in _TOOL_NAMES]
    fresh = [c for c in response.tool_calls if c["name"] in _TOOL_NAMES and _call_key(c) not in done_before]
    if fresh and response.tool_calls:
        response.tool_calls = fresh + invented  # invented ones get the tool's 'not a valid tool' error
        return response
    hint = " ".join(json.dumps(c["args"], ensure_ascii=False) for c in invented if c["args"])
    text = str(response.content or "").strip()
    last = next((str(m.content) for m in reversed(messages) if isinstance(m, ToolMessage)
                 and not str(m.content).startswith(("ERROR", "Error"))), "")
    outputs = [str(m.content) for m in messages if isinstance(m, ToolMessage) and m.name != "find_entity"
               and not str(m.content).startswith(("ERROR", "Error"))]
    if len(outputs) > 1:  # several results (e.g. one per month): the answer is chosen from them all, not the last
        conclusion = "\n".join(x for x in (text, hint) if x) or \
            "No final reply was written. The tool outputs were (answer the question from them, following the plan):"
        return AIMessage(content=conclusion + "\n" + "\n".join(outputs))
    conclusion = "\n".join(x for x in (text, hint) if x) or "The answer is the result of the last successful tool call:"
    return AIMessage(content=conclusion + ("\n" + last if last else ""))


def agent(state: QueryState, runtime: Runtime[QueryContext]) -> Command[Literal["tools", "verify", "finalize"]]:
    """Let the LLM pick the next calls; once it replies without tool calls it has concluded."""
    if runtime.context.router and _task_answer(state["messages"]) is not None and \
            isinstance(state["messages"][-1], ToolMessage):
        # a task tool already gave the final answer: nothing left to do but (optionally) check its call
        return Command(goto="verify" if runtime.context.verify else "finalize")
    task_tool = TASK_TOOLS.get(state.get("task") or "")
    task_tools = [task_tool] if task_tool is not None else []
    task_tools += [TASK_TOOLS[f] for f in _SIBLINGS.get(state.get("task") or "", ())]
    if state.get("checks"):  # after a failed check the right tool may be another family's
        task_tools = task_tools + [t for t in TASK_TOOLS.values() if t not in task_tools]  # tools are unhashable
    llm = runtime.context.model.bind_tools(TOOLS + task_tools)
    if not runtime.context.agent_think:
        llm = llm.bind(extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    opening = _opening_messages(state, runtime.context)
    if not runtime.context.agent_think:  # thinking off: the steps it would think through, written out (agent only)
        opening[-1] = HumanMessage(opening[-1].content + AGENT_OFF)
    response = llm.invoke([*opening, *state["messages"]])
    response = repair_tool_calls(response)
    response = repair_task_calls(response, state)
    response = conclude_if_done(response, state["messages"])
    if response.tool_calls:
        return Command(update={"messages": [response]}, goto="tools")
    # general path (information_finder + calculate): check the calls behind the reply too, like a task tool's
    check = runtime.context.router and runtime.context.verify and runtime.context.verify_general and \
        state.get("checks", 0) < 2 and \
        any(isinstance(m, ToolMessage) and m.name == "information_finder" for m in state["messages"])
    return Command(update={"messages": [response]}, goto="verify" if check else "finalize")


def _check_period(spec, state) -> str:
    """The real dates a period object covers, so the agent sees what the analyze step's reading means."""
    try:
        w = resolve_period(Period.model_validate(spec).spec(), dt.date.fromisoformat(state["as_of_date"]),
                           _lunar(state["calendar_path"]))
        return f"{w.start or 'start of data'} → {w.end}"
    except Exception as e:  # noqa: BLE001 - shown to the agent, which fixes the period itself
        return f"invalid period ({e})"


def analyze(state: QueryState, runtime: Runtime[QueryContext]) -> Command[Literal["agent", "branches", "finalize", "__end__"]]:
    """Read the question before any tool call: clarify it, check scope, plan the calls (no hard-coded rules)."""
    if not runtime.context.analyze:
        return Command(goto="agent")
    if runtime.context.router:
        return _route(state, runtime.context)
    if runtime.context.branches > 0:
        return _insight(state, runtime.context)
    prompt = [*_opening_messages(state, runtime.context), HumanMessage(ANALYZE.format(period_kinds=Period.model_fields["kind"].description))]
    try:
        text = str(runtime.context.model.invoke(prompt).content)
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - no reading: the agent works from the question alone
        return Command(goto="agent")
    if str(data.get("scope", "")).lower() == "out":
        return Command(update={"analysis": json.dumps(data, ensure_ascii=False),
                               "messages": [AIMessage(OUT_OF_SCOPE + ": " + str(data.get("scope_reason", "")))]},
                       goto="finalize")
    if isinstance(data.get("period"), dict):
        data["period_dates"] = _check_period(data["period"], state)
    data.pop("scope_reason", None)
    return Command(update={"analysis": json.dumps(data, ensure_ascii=False)}, goto="agent")


# What a router "not_recorded" verdict may name. Shop columns are checked against the shop's manifest (the router
# only proposes); the rest is information sales records never hold, for any shop.
# payment_method / seller are left out: "bán chịu", "trả góp" ... are debt records the full data has (→ abstain).
_SHOP_COLUMNS = {"product_group": "product_group", "customer_group": "customer_group", "channel": "channel",
                 "order_time": "order_time", "cost_price": "cost_price"}
_NEVER_RECORDED = {"customer_gender": "customer gender is not recorded", "customer_address":
                   "customer addresses are not recorded", "reason": "cancellation / return reasons are not recorded",
                   "opinion": "subjective: opinions (taste, quality, friendliness...) are not recorded",
                   "advice": "advice is not in the shop data"}


def _not_recorded(state: QueryState, needs: str) -> str | None:
    """The NO DATA reason when `needs` is really missing for this shop, else None (then the agent goes on)."""
    if needs in _SHOP_COLUMNS:
        col = _SHOP_COLUMNS[needs]
        return None if _shop(state["shop_dir"]).has(col) else f"this shop does not record {col}"
    return _NEVER_RECORDED.get(needs)


def _no_think(model, max_tokens: int | None = None):
    """The model with Qwen thinking off (vLLM chat template switch), optionally with an output cap."""
    kw = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return model.bind(**kw, **({"max_tokens": max_tokens} if max_tokens else {}))


def _call_record(node: str, reply, seconds: float, mode: str) -> dict:
    meta = getattr(reply, "usage_metadata", None) or {}
    return {"node": node, "mode": mode, "seconds": round(seconds, 2), "input": meta.get("input_tokens", 0),
            "output": meta.get("output_tokens", 0)}


def _budget_reply(context: QueryContext, prompt: list, node: str) -> tuple[str, list]:
    """Thinking with a budget (vLLM + Qwen): think for at most think_cap tokens; if the thinking is cut off, close it
    (</think>) and let the model write the reply from what it has thought so far, without thinking further.
    The decision is usually made early in the thinking; the rest is re-checking, which costs time on a small GPU."""
    from langchain_openai.chat_models.base import _convert_message_to_dict
    model = context.model
    client, name = model.root_client, model.model_name
    conv = [_convert_message_to_dict(m) for m in prompt]
    common = {"model": name, "temperature": model.temperature if model.temperature is not None else 0.0}
    start = time.perf_counter()
    r = client.chat.completions.create(messages=conv, max_tokens=context.think_cap, **common)
    calls = [{"node": node, "mode": "budget", "seconds": round(time.perf_counter() - start, 2),
              "input": r.usage.prompt_tokens, "output": r.usage.completion_tokens}]
    msg, extra = r.choices[0].message, r.choices[0].message.model_extra or {}
    if r.choices[0].finish_reason == "stop" and msg.content:
        return msg.content, calls
    thought = str(extra.get("reasoning") or extra.get("reasoning_content") or "").rstrip()
    start = time.perf_counter()
    r = client.chat.completions.create(messages=conv + [{"role": "assistant", "content": f"<think>\n{thought}\n</think>\n\n"}],
                                       max_tokens=1024, extra_body={"continue_final_message": True,
                                                                    "add_generation_prompt": False}, **common)
    calls.append({"node": node, "mode": "budget_close", "seconds": round(time.perf_counter() - start, 2),
                  "input": r.usage.prompt_tokens, "output": r.usage.completion_tokens})
    return r.choices[0].message.content or "", calls


def _json_reply(context: QueryContext, prompt: list, think: str, node: str) -> tuple[dict, list]:
    """One JSON object from the model, trying the routes of `think` in order (see QueryContext.router_think).
    Returns the object and a record of each call; ValueError(records) when no route gives valid JSON."""
    model, cap = context.model, context.think_cap
    routes = {"on": [("think", model), ("no_think", _no_think(model))],
              "capped": [("think_capped", model.bind(max_tokens=cap)), ("no_think", _no_think(model))],
              "off": [("no_think", _no_think(model, 1024)), ("think_capped", model.bind(max_tokens=cap))],
              "prompt": [("no_think", _no_think(model, 1024)), ("no_think", _no_think(model, 1024))],
              "budget": []}[think]
    calls = []
    if think == "budget":  # one bounded thinking pass; a parse failure falls back to thinking off
        try:
            text, calls = _budget_reply(context, prompt, node)
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            if isinstance(data, dict):
                return data, calls
        except Exception:  # noqa: BLE001 - e.g. no JSON, connection error: thinking off below
            pass
        routes = [("no_think", _no_think(model, 1024))]
    for mode, llm in routes:
        start = time.perf_counter()
        try:
            reply = llm.invoke(prompt)
        except Exception:  # noqa: BLE001 - e.g. timeout: try the next route
            calls.append({"node": node, "mode": mode, "seconds": round(time.perf_counter() - start, 2), "error": True})
            continue
        calls.append(_call_record(node, reply, time.perf_counter() - start, mode))
        text = str(reply.content)
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data, calls
    raise ValueError(calls)


def _recheck_quarter(state: QueryState, context: QueryContext, data: dict) -> list:
    """The router sometimes reads T3/2025 (month 3) as quý 3. When it picks a quarter, one short no-think question asks
    whether the question really names a quarter; if not, the period becomes month <n> of the same year."""
    p = data.get("period")
    if not (isinstance(p, dict) and p.get("kind") == "quarter" and p.get("quarter")):
        return []
    prompt = [HumanMessage(QUARTER_CHECK.format(question=state["question"], words=json.dumps(p, ensure_ascii=False)))]
    try:
        got, calls = _json_reply(context, prompt, "off", "quarter_check")
    except ValueError as e:
        return e.args[0] if e.args and isinstance(e.args[0], list) else []
    if got.get("quarter_named") is False:
        data["period"] = {"kind": "month", "month": _int_or_keep(p["quarter"]), "year": p.get("year")}
        cq = str(data.get("clarified_question", ""))
        data["clarified_question"] = cq + f" (period: month {p['quarter']}" + (f"/{p['year']}" if p.get("year") else "") + ")"
    calls[-1]["quarter_named"] = got.get("quarter_named")
    return calls


def _route(state: QueryState, context: QueryContext) -> Command:
    """Router: scope check, the task family of the question and a clarified wording; nothing else is planned here."""
    # thinking off ("prompt"): its own prompt, the reasoning written step by step in the JSON (see ROUTE_OFF)
    route = (ROUTE_OFF if context.router_think == "prompt" else ROUTE).format(
        period_kinds=Period.model_fields["kind"].description)
    prompt = [*_opening_messages(state, context), HumanMessage(route)]
    try:
        data, calls = _json_reply(context, prompt, context.router_think, "route")
    except ValueError as e:  # no routing: the agent works with the general tools
        return Command(update={"llm_calls": e.args[0] if e.args and isinstance(e.args[0], list) else []},
                       goto="agent")
    needs = str(data.get("needs", "")).strip()
    scope = str(data.get("scope", "")).lower()
    # information no sales record holds (gender, address, reasons, opinions, advice): unanswerable whatever the scope
    # label; a shop column: unanswerable only when the router says so and the shop's manifest confirms it
    # (a "needs" next to scope "in" is noise: seen with thinking off, e.g. needs "reason" on a debt question)
    missing = (_NEVER_RECORDED.get(needs) if scope in ("not_recorded", "out") else None) or \
        (_not_recorded(state, needs) if scope == "not_recorded" else None)
    if missing:  # §9: unanswerable scores when the data really does not hold it
        return Command(update={"llm_calls": calls, "analysis": json.dumps(data, ensure_ascii=False),
                               "submission": {"type": "unanswerable", "reason": "NO DATA: " + missing}}, goto=END)
    if scope == "out":
        return Command(update={"llm_calls": calls, "analysis": json.dumps(data, ensure_ascii=False),
                               "messages": [AIMessage(OUT_OF_SCOPE + ": " + str(data.get("scope_reason", "")))]},
                       goto="finalize")
    task = str(data.get("task", "other"))
    calls += _recheck_quarter(state, context, data)
    keys = ("clarified_question", "period", "answer_type")
    if context.router_think == "prompt":  # thinking off: the agent also follows the router's quoted filters
        keys += ("measure_filters",)
    reading = {k: data[k] for k in keys if data.get(k)}
    if isinstance(reading.get("period"), dict):
        reading["period_dates"] = _check_period(reading["period"], state)
    return Command(update={"llm_calls": calls, "task": task, "analysis": json.dumps(reading, ensure_ascii=False)}, goto="agent")


def _insight(state: QueryState, context: QueryContext) -> Command:
    """Branching analyze: insight on the question and 1..branches readings of it, each run later as its own branch."""
    prompt = [*_opening_messages(state, context), HumanMessage(INSIGHT.format(
        max_branches=context.branches, period_kinds=Period.model_fields["kind"].description))]
    try:
        text = str(context.model.invoke(prompt).content)
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    except Exception:  # noqa: BLE001 - no reading: one branch on the question alone
        data = {}
    if str(data.get("scope", "")).lower() == "out":
        return Command(update={"analysis": json.dumps(data, ensure_ascii=False),
                               "messages": [AIMessage(OUT_OF_SCOPE + ": " + str(data.get("scope_reason", "")))]},
                       goto="finalize")
    readings = [r for r in data.get("interpretations") or [] if isinstance(r, dict)][:context.branches] or [{}]
    for r in readings:
        if isinstance(r.get("period"), dict):
            r["period_dates"] = _check_period(r["period"], state)
    return Command(update={"insight": str(data.get("insight", "")), "readings": readings}, goto="branches")


def _run_branch(state: QueryState, context: QueryContext, reading: dict) -> dict:
    """One reading through the plain agent ⇄ tools loop; returns its messages (and the error, if any)."""
    sub = {**state, "messages": [], "results": {}, "analysis": json.dumps(reading, ensure_ascii=False) if reading else ""}
    try:
        out = branch_agent.invoke(sub, context=context, config={"recursion_limit": BRANCH_RECURSION_LIMIT})
        return {"reading": reading, "messages": out["messages"], "error": None}
    except Exception as e:  # noqa: BLE001 - a failed branch is just not chosen
        return {"reading": reading, "messages": [], "error": f"{type(e).__name__}: {e}"[:200]}


def branches(state: QueryState, runtime: Runtime[QueryContext]) -> dict:
    """Run every reading in parallel (at most context.branches threads)."""
    readings = state.get("readings") or [{}]
    with ThreadPoolExecutor(len(readings)) as pool:
        runs = list(pool.map(lambda r: _run_branch(state, runtime.context, r), readings))
    return {"branch_runs": runs}


def _branch_text(i: int, run: dict) -> str:
    r = run["reading"]
    lines = [f"## Reading {i}: {r.get('clarified_question', '(the question as written)')}",
             f"plan: {r.get('plan', '')} | answer_type: {r.get('answer_type', '')}"]
    for m in run["messages"]:
        if isinstance(m, ToolMessage) and m.name != "find_entity":
            lines.append("tool: " + str(m.content)[:1500])
    final = next((str(m.content) for m in reversed(run["messages"]) if isinstance(m, AIMessage) and not m.tool_calls), "")
    lines.append("conclusion: " + (final[:1500] if final else f"none ({run['error'] or 'no reply'})"))
    return "\n".join(lines)


def synthesize(state: QueryState, runtime: Runtime[QueryContext]) -> Command[Literal["finalize", "__end__"]]:
    """Pick the reading that fits the question; its messages become the run's messages for finalize."""
    runs = state.get("branch_runs") or []
    usable = [i for i, r in enumerate(runs) if r["messages"] and not r["error"]]
    if not usable:
        return Command(update={"submission": {"type": "abstain"}, "choice": 0}, goto=END)
    if len(runs) == 1:
        choice = 1
    else:
        text_in = "\n\n".join(_branch_text(i + 1, r) for i, r in enumerate(runs))
        prompt = [*_opening_messages(state, runtime.context),
                  HumanMessage((("Insight: " + state["insight"] + "\n") if state.get("insight") else "") +
                               SYNTHESIZE.format(branches=text_in))]
        try:
            text = str(runtime.context.model.invoke(prompt).content)
            choice = int(json.loads(text[text.find("{"):text.rfind("}") + 1]).get("choice", 0))
        except Exception:  # noqa: BLE001 - cannot tell: take the most likely reading if it ran fine
            choice = 1 if 0 in usable else 0
    if not 1 <= choice <= len(runs) or (choice - 1) not in usable:
        return Command(update={"submission": {"type": "abstain"}, "choice": 0}, goto=END)
    run = runs[choice - 1]
    return Command(update={"messages": run["messages"], "choice": choice,
                           "analysis": json.dumps(run["reading"], ensure_ascii=False) if run["reading"] else ""},
                   goto="finalize")


class Submission(BaseModel):
    type: Literal["number", "percent", "date", "boolean", "string", "list", "table", "unanswerable", "abstain"] = Field(
        description="The answer type from rulebook §9, or abstain.")
    answer_json: str = Field(default="", description="The answer value as JSON text, e.g. 89526400, 12.4, "
                             "\"2026-08-15\", true, \"Sting dâu 330ml\", [\"A\", \"B\"]. Empty for unanswerable / abstain.")
    reason: str = Field(default="", description="Only for unanswerable: the NO DATA message.")


def _no_data_seen(messages) -> bool:
    return any(isinstance(m, ToolMessage) and str(m.content).startswith("NO DATA") for m in messages)


def _submission_from_text(llm, prompt) -> Submission:
    schema = json.dumps(Submission.model_json_schema(), ensure_ascii=False)
    text = str(llm.invoke([*prompt, HumanMessage(
        "Reply with only one JSON object matching this schema, no other text:\n" + schema)]).content)
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    # the model often writes the value itself ("answer_json": 166285 / ["A", "B"]) instead of JSON text: same meaning
    if "answer_json" in data and not isinstance(data["answer_json"], str):
        data["answer_json"] = json.dumps(data["answer_json"], ensure_ascii=False)
    if not isinstance(data.get("reason", ""), str):
        data["reason"] = json.dumps(data["reason"], ensure_ascii=False)
    return Submission.model_validate(data)


def _finalize_self_hosted(model, prompt) -> Submission:
    """vLLM 0.33 + Qwen3.5-9B: guided json_schema returns garbage (1/6 right, 2026-11-10), JSON asked in plain text
    is 6/6. So: JSON in text with thinking off (~1s); if it does not parse, again with thinking on; else abstain."""
    out = Submission(type="abstain")
    for llm in (model.bind(extra_body={"chat_template_kwargs": {"enable_thinking": False}}), model):
        try:
            out = _submission_from_text(llm, prompt)
            break
        except Exception:  # noqa: BLE001 - not valid JSON: try the next route
            continue
    out.reason, out.answer_json = out.reason.strip(), out.answer_json.strip()
    return out


def _reconcile_type(state, context, prompt, out: Submission) -> Submission:
    """Step after finalize (self-hosted): when the submission's type differs from the type the analyze step expected,
    let the model look again with both in view; it keeps or changes the type itself."""
    if not (context.analyze and context.finalize_no_think and state.get("analysis")) or out.type in ("abstain",
                                                                                                    "unanswerable"):
        return out
    try:
        expected = json.loads(state["analysis"])
    except json.JSONDecodeError:
        return out
    want, fmt = str(expected.get("answer_type", "")).strip(), str(expected.get("answer_format", "")).strip()
    if not want or want == out.type:
        return out
    note = HumanMessage(RECHECK_TYPE.format(type=out.type, answer=out.answer_json, want=want, fmt=fmt))
    try:
        return _finalize_self_hosted(context.model, [*prompt, note])
    except Exception:  # noqa: BLE001
        return out


_TASK_TOOL_NAMES = {t.name for t in TASK_TOOLS.values()}


def _last_task_call(messages) -> tuple[dict, str] | None:
    """The newest task tool call that gave an answer, and its output."""
    outputs = {m.tool_call_id: str(m.content) for m in messages if isinstance(m, ToolMessage)}
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            for c in reversed(m.tool_calls):
                if c["name"] in _TASK_TOOL_NAMES and answer_from(outputs.get(c["id"], "")):
                    return c, outputs[c["id"]]
    return None


# Tools whose call the check does not judge well: price_change_effect finds the day of the price change itself, and
# the check took that day for a mismatch with "today" (4 of 20 right answers flagged, 0 wrong ones, 2026-11-12)
_NO_VERIFY = {"price_change_effect"}


def _finder_calls(messages) -> tuple[dict, str] | None:
    """General path: the information_finder / calculate calls of the run with their outputs, and the final reply,
    shown to the check as one call."""
    outputs = {m.tool_call_id: str(m.content) for m in messages if isinstance(m, ToolMessage)}
    calls = [c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls
             if c["name"] in ("information_finder", "calculate")]
    if not calls or not isinstance(messages[-1], AIMessage) or messages[-1].tool_calls:
        return None
    # the lookups behind the keys in the filters: without them a product key looks unverified
    shown = [f"find_entity({json.dumps(c['args'], ensure_ascii=False)}) → best match:\n"
             + "\n".join(outputs.get(c["id"], "").splitlines()[:2])
             for m in messages if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] == "find_entity"]
    for c in calls[-6:]:
        out = outputs.get(c["id"], "")
        shown.append(f"{c['name']}({json.dumps(c['args'], ensure_ascii=False)}) →\n"
                     + (out[:1200] + " ..." if len(out) > 1200 else out))
    reply = str(messages[-1].content).strip()
    return {"name": "information_finder", "args": {}}, "\n".join(shown) + "\nFinal reply: " + reply[:600]


def verify(state: QueryState, runtime: Runtime[QueryContext]) -> Command[Literal["agent", "finalize", "__end__"]]:
    """Check (thinking off, one short reply) that the task tool's call matches the question. A mismatch sends the
    agent back once with the problem in view; a second mismatch → abstain (0) instead of a likely wrong answer."""
    context, found = runtime.context, _last_task_call(state["messages"])
    if found is None:
        found = _finder_calls(state["messages"])
    if found is None or found[0]["name"] in _NO_VERIFY:
        return Command(goto="finalize")
    call, output = found
    shown = f"{call['name']}({json.dumps(call['args'], ensure_ascii=False)})" if call["args"] else \
        "the information_finder / calculate calls below"
    doc = next((t.description for t in TASK_TOOLS.values() if t.name == call["name"]), "")
    if doc:  # the system prompt describes only the general tools
        shown += f"\nWhat {call['name']} does: " + " ".join(doc.split())
    # the check reads the question itself, not the router's reading of it: an independent second reading
    plain = {**state, "analysis": "", "task": ""}
    prompt = [*_opening_messages(plain, context), HumanMessage(VERIFY.format(call=shown, output=output))]
    try:
        data, calls = _json_reply(context, prompt, "off", "verify")
    except ValueError as e:  # no verdict: keep the answer
        return Command(update={"llm_calls": e.args[0] if e.args and isinstance(e.args[0], list) else []},
                       goto="finalize")
    checks = state.get("checks", 0) + 1
    calls[-1]["verdict"] = str(data.get("verdict", "")).lower()
    calls[-1]["problem"] = str(data.get("problem", ""))
    if calls[-1]["verdict"] != "wrong":
        return Command(update={"llm_calls": calls, "checks": checks}, goto="finalize")
    if checks > 1:
        return Command(update={"llm_calls": calls, "checks": checks, "submission": {"type": "abstain"}}, goto=END)
    note = HumanMessage(VERIFY_RETRY.format(tool=call["name"], problem=calls[-1]["problem"]))
    return Command(update={"llm_calls": calls, "checks": checks, "messages": [note]}, goto="agent")


def _task_answer(messages) -> dict | None:
    """The answer of the latest turn's task tool calls (the last one with an answer), if that turn had any."""
    turn = []
    for m in reversed(messages):
        if not isinstance(m, ToolMessage):
            break
        turn.append(m)
    for m in turn:  # newest first
        got = answer_from(m.content)
        if got:
            return got
    return None


def finalize(state: QueryState, runtime: Runtime[QueryContext]) -> dict:
    """Convert the agent's final reply into a submission dict in the rulebook's format."""
    if OUT_OF_SCOPE in str(state["messages"][-1].content):
        return {"submission": {"type": "abstain"}}
    if runtime.context.router:  # a task tool's output is the answer: no LLM step to retype it
        got = _task_answer(state["messages"])
        if got and got.get("type") and "answer" in got:
            return {"submission": {"type": got["type"], "answer": got["answer"]}}
    # json_schema: the provider's native structured output (forced tool calling is unsupported on claude-sonnet-5-5)
    prompt = [*_opening_messages(state, runtime.context), *state["messages"], HumanMessage(FINALIZE)]
    if runtime.context.finalize_no_think:
        out = _finalize_self_hosted(runtime.context.model, prompt)
    else:
        llm = runtime.context.model.with_structured_output(Submission, method="json_schema")
        out = llm.invoke(prompt)

    out = _reconcile_type(state, runtime.context, prompt, out)
    if out.type == "abstain":
        return {"submission": {"type": "abstain"}}
    if out.type == "unanswerable":
        # −1 if wrong: only trust it when a tool really reported missing data
        if not _no_data_seen(state["messages"]):
            return {"submission": {"type": "abstain"}}
        if runtime.context.router:  # the router's gate already submits the real "not recorded" cases; a tool's NO DATA
            return {"submission": {"type": "abstain"}}  # here is mostly a wrong filter or period (net < 0 in 5 runs)
        return {"submission": {"type": "unanswerable", "reason": out.reason or "no data"}}
    try:
        answer = json.loads(out.answer_json)
    except json.JSONDecodeError:
        answer = out.answer_json  # not valid JSON: a bare string answer is the only safe reading
    if (out.type in ("number", "percent") and not isinstance(answer, (int, float))) or \
            (isinstance(answer, bool) and out.type != "boolean"):
        return {"submission": {"type": "abstain"}}  # malformed number: abstaining scores 0, a guess −1
    if isinstance(answer, float) and answer.is_integer():
        answer = int(answer)
    return {"submission": {"type": out.type, "answer": answer}}


builder = StateGraph(QueryState, context_schema=QueryContext)
builder.add_node(analyze)
builder.add_node(agent)
def _tool_error(e: Exception) -> str:
    """Tool error shown to the LLM, with the right call shape so the retry is not a guess."""
    return (f"Error: {e}\nCall shape: information_finder({{\"measure\": \"revenue\", \"period\": {{\"kind\": \"month\", "
            f"\"month\": 5, \"year\": 2026}}, \"filters\": {{\"product_keys\": [\"...\"]}}}}) - period is one nested "
            f"object. Fix the arguments and call again.")


builder.add_node("tools", ToolNode(TOOLS + list(TASK_TOOLS.values()), handle_tool_errors=_tool_error))
builder.add_node(finalize)
builder.add_node(verify)
builder.add_node(branches)
builder.add_node(synthesize)
builder.add_edge(START, "analyze")
builder.add_edge("tools", "agent")
builder.add_edge("branches", "synthesize")
builder.add_edge("finalize", END)
query_agent = builder.compile()

# One branch = the plain agent ⇄ tools loop; its "finalize" only ends the branch (synthesize then finalize run once)
BRANCH_RECURSION_LIMIT = 14
_branch = StateGraph(QueryState, context_schema=QueryContext)
_branch.add_node(agent)
_branch.add_node("tools", ToolNode(TOOLS + list(TASK_TOOLS.values()), handle_tool_errors=_tool_error))
_branch.add_node("finalize", lambda state: {})
_branch.add_node("verify", lambda state: {})  # never reached: branches run without the router
_branch.add_edge("verify", "finalize")
_branch.add_edge(START, "agent")
_branch.add_edge("tools", "agent")
_branch.add_edge("finalize", END)
branch_agent = _branch.compile()
