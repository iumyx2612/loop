"""Step 4, report_missing: the sales_lines columns the shop does not record anywhere (plain code, no LLM; agreed with
the user, 2026-11-09).

Found = step 2 mapped it (state["columns"]), or at least one attached table filled it in step 3's snapshot (passed its
column check). The field resolve is postponed, so one passing candidate is enough.

Only plan_check.MAPPABLE fields can be missing: plan_check wants each of them mapped or listed missing. An OPTIONAL
field that is not found (qty_base, line_discount, customer_code) is left out: the converter computes it or does
without it.

Output, state["missing_columns"]: {field: reason} (the user's choice: the reason stays with the field; the plan's
SalesLinePlan.missing_columns is its keys). A reason starts with one code, then the detail:
- line_only         open in the line table, and no other table can hold it (fill_missing_schema.LINE_ONLY)
- failed_check      step 2 named a line column, but it failed its column check (+ step 2's problem)
- unreached         fillable, but no table attached by an anchor that looks it up (anchors.ANCHOR_MAP)
- not_in_tables     attached tables were asked and none named a column (or their call failed)
- failed_in_tables  attached tables named a column, but it failed the column check
A fillable field whose line column failed step 2 gets step 3's code, with step 2's problem added to the detail.

The trace record also has `problems`: an ALWAYS_RECORDED field (order_date, line_amount) is missing, which is almost
surely an earlier step's mistake, not a real gap; and `known_gaps`: missing fields a later step is expected to fill
(unit from the README / base unit when the line has no unit column; base_unit from the conversions, postponed).
"""
import time

from src.datamate.analyst_agent.explore_agent.plan_check import ALWAYS_RECORDED, MAPPABLE
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.anchors import ANCHOR_MAP
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import FILLABLE
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState

# missing fields a later step should fill: not a real gap yet, shown apart in the trace
KNOWN_GAPS = {
    "unit": "no unit column on the line: the reference takes it from the README list or the base unit (biz_128, "
            "dev_007, the phone shops); not built",
    "base_unit": "may come from the conversion table or the README list (postponed, user 2026-11-09)",
}


def known_gaps(missing: dict[str, str]) -> dict[str, str]:
    """The missing fields that are KNOWN_GAPS. Only on shops with product lines: a shop recording one row per order
    or ticket (biz_110, dev_009, dev_012) has no unit when it has no qty, and no base unit when it has no product."""
    has = {"unit": "qty" not in missing,
           "base_unit": "product_code" not in missing or "product_name" not in missing}
    return {f: KNOWN_GAPS[f] for f in missing if f in KNOWN_GAPS and has[f]}


def step2_problem(field: str, problems: list[str]) -> str | None:
    """Step 2's problem(s) about `field`: a problem starts with its answers, one `field ← 'column'` per line
    (column_mapping.checks.answer_lines)."""
    found = [p for p in problems if any(line.startswith(f"{field} ← ") for line in p.splitlines())]
    return " / ".join(" | ".join(p.splitlines()) for p in found) or None


def _snapshot_calls(state: WorkflowState) -> dict[str, str]:
    """table -> why its snapshot call gave nothing (call failed / unreadable reply), from the last snapshot record."""
    for r in reversed(state.get("trace") or []):
        if r.get("step") == "snapshot":
            return {s["table"]: ("call failed" if s.get("call_failed") else "unreadable reply")
                    for s in r.get("snapshots", []) if s.get("call_failed") or s.get("error")}
    return {}


def step3_reason(field: str, snapshots: list[dict], bad_calls: dict[str, str]) -> str:
    """Why step 3 found no column for a fillable field."""
    asked = [s for s in snapshots if field in s["asked"]]
    if not asked:
        anchors = ", ".join(a for a, fields in ANCHOR_MAP.items() if field in fields)
        return f"unreached: no table attached by {anchors}"
    failed = [f"[{s['table']}] {s['failed'][field]}".replace("\n", " | ") for s in asked if field in s["failed"]]
    if failed:
        return "failed_in_tables: " + "; ".join(failed)
    tables = ", ".join(f"[{s['table']}]" + (f" ({bad_calls[s['table']]})" if s["table"] in bad_calls else "")
                       for s in asked)
    return f"not_in_tables: asked {tables}; none holds it"


def missing_reasons(state: WorkflowState) -> dict[str, str]:
    """{field: reason} for every MAPPABLE field neither step 2 nor step 3 found, in MAPPABLE order."""
    mapped = {c.schema_column for c in state.get("columns") or []}
    open_columns = set(state.get("open_columns") or [])
    problems = state.get("column_problems") or []
    snapshots = state.get("snapshots") or []
    filled = {f for s in snapshots for f in s["filled"]}
    bad_calls = _snapshot_calls(state)
    snapshot_ran = "snapshots" in state

    out = {}
    for f in MAPPABLE:
        if f in mapped or f in filled:
            continue
        # step 2 left it open ("(not in this file)") or its answer failed the column check on every attempt
        failed = None if f in open_columns else (
            step2_problem(f, problems) or "step 2 named a column but left it unmapped (no problem recorded)")
        if f not in FILLABLE:
            out[f] = f"failed_check: {failed}" if failed else \
                "line_only: not in the line table, and no other table can hold it"
            continue
        reason = step3_reason(f, snapshots, bad_calls) if snapshot_ran else "unreached: step 3 (snapshot) did not run"
        out[f] = reason + (f"; the line's column also failed step 2: {failed}" if failed else "")
    return out


def report_missing(state: WorkflowState) -> dict:
    """The parent graph's node for step 4 (named report_missing: a node cannot share its name with the state key
    `missing_columns`)."""
    start = time.perf_counter()
    missing = missing_reasons(state)
    record = {"step": "report_missing", "missing_columns": missing,
              "problems": [f"{f} is missing, but every shop records it: probably an earlier step's mistake ({r})"
                           for f, r in missing.items() if f in ALWAYS_RECORDED],
              "known_gaps": known_gaps(missing)}
    if not state.get("line_tables"):
        record["problems"].insert(0, "no line tables (step 1 chose none): every field is missing")
    record["seconds"] = since(start)
    return {"missing_columns": missing, "trace": [record]}
