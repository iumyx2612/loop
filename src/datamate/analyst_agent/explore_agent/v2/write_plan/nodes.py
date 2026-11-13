"""Step 8, write_plan (code, no LLM; agreed with the user, 2026-11-11): put the SalesLinePlan together from the
steps' state fields, then check it with plan_check (the guard; problems go to the trace, no retry yet).

Where each plan field comes from:
- line_tables (1); columns = step 2's mapping + the snapshot's fills (3); missing_columns = step 4's keys;
  joins = attach's links (3); statuses, blank_status (5); forward_fill_columns, return_lines, notes (6);
  service_fee_names, non_revenue_names (7); subtotal_labels = step 0's regexes of the line table group;
  skipped_rows = step 0's rows of the line and joined tables; header_rows = confirm_headers' rows (so a converter
  reads the tables as the steps did); unmapped (8a, unmapped.py).
- extra_attributes = [] (postponed: the schema has no columns for them yet).
- conversions: state["conversions"] (map_conversions, v2/conversions/); a state saved before that step gets [] and a
  note saying so. With [], a converter built from the plan puts unit_factor = 1 on big-unit lines unless qty_base is
  mapped.

Built as Claude chose (not discussed yet):
- A field filled by several attached tables (the field resolve is not built): the first by attach round wins; the
  others are in the trace (`alternatives`).
- One Join per attached table, by its first link; its other links are in the trace (`extra_links`).
- A code + name pair (attach.PAIRS, agreed with the user 2026-11-11): one Join with key = the first column of the
  pair and alt_key = the other; a row attaches when the line's column equals either.
- A table group is named by one TableRef: every sheet of an xlsx = sheet None; a group spread over several files
  (rare outside the line tables) is named by its first table, with a trace warning.
"""
import time
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.plan_check import check_plan
from src.datamate.analyst_agent.explore_agent.state import ColumnSource, Join, SalesLinePlan, TableRef
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import shop_groups
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import named_groups
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.write_plan.unmapped import unmapped

CONVERSIONS_NOTE = ("conversions not explored (the state has no conversions step): conversions = [] is a placeholder; "
                    "a conversion table or README list, if the shop has one, is not in this plan")


def table_refs(shop_dir: Path, g: explore.TableGroup) -> list[TableRef]:
    """The group's tables as TableRefs: one per file when it covers every sheet of an xlsx, else one per sheet."""
    by_file: dict[str, list] = {}
    for t in g.tables:
        by_file.setdefault(t.file, []).append(t.sheet)
    out = []
    for f, sheets in by_file.items():
        if f.lower().endswith(".xlsx") and len(sheets) > 1 and set(sheets) == set(explore._sheets(shop_dir, f)):
            out.append(TableRef(file=f, sheet=None))
        else:
            out += [TableRef(file=f, sheet=s) for s in sheets]
    return out


def build_plan(state: WorkflowState) -> tuple[SalesLinePlan, dict]:
    """The plan and the trace details (alternatives, extra links, warnings, used columns)."""
    shop_dir = Path(state["shop_dir"])
    info: dict = {"alternatives": {}, "extra_links": [], "warnings": []}
    groups = shop_groups(state)
    line_name = next((n for n, _, is_line in named_groups(shop_dir, state["header_rows"], state["line_tables"],
                                                         state.get("skipped_rows")) if is_line), None)
    refs = {name: table_refs(shop_dir, g) for name, g in groups.items()}
    line_ref = state["line_tables"][0] if state["line_tables"] else None

    def ref_of(name: str) -> TableRef:
        if name == line_name and line_ref is not None:
            return line_ref
        if len(refs[name]) > 1:
            info["warnings"].append(f"{name}: spread over {len(refs[name])} tables, named by the first "
                                    f"({refs[name][0].file} {refs[name][0].sheet or ''})".rstrip())
        return refs[name][0]

    # used columns per group, for unmapped
    columns = list(state.get("columns") or [])
    used: dict[str, set[str]] = {}
    if line_name:
        used[line_name] = {c.raw_column for c in columns} | set(state.get("forward_fill_columns") or [])

    # joins: one per attached table, by its first link (attached is in round order: a table's on_file is earlier)
    joins, notes = [], list(state.get("notes") or [])
    for a in state.get("attached") or []:
        if a["round"] == 0 or not a["links"]:
            continue
        name, link = a["table"], a["links"][0]
        used.setdefault(name, set()).update(k["column"] for k in a["links"])
        for k in a["links"]:
            used.setdefault(k["to_table"], set()).add(k["to_column"])
        info["extra_links"] += [f"{name}: {k['column']!r} -> {k['to_table']} {k['to_column']!r} ({k['anchor']})"
                                for k in a["links"][1:] if k.get("pair_with") is None]
        me, on = ref_of(name), ref_of(link["to_table"])
        # a code + name pair (attach.PAIRS): one join on either column (Join.alt_key)
        joins.append(Join(file=me.file, sheet=me.sheet, key=link["column"], on_file=on.file, on_sheet=on.sheet,
                          on_key=link["to_column"], alt_key=link.get("pair_with")))

    # columns: step 2's, then each open field from the first attached table that filled it
    have = {c.schema_column for c in columns}
    attached_names = {a["table"] for a in state.get("attached") or []}
    for s in state.get("snapshots") or []:
        if s["table"] not in attached_names:
            continue
        r = ref_of(s["table"])
        for field, col in (s.get("filled") or {}).items():
            if field in have:
                info["alternatives"].setdefault(field, []).append(f"{s['table']} / {col}")
                continue
            have.add(field)
            columns.append(ColumnSource(schema_column=field, file=r.file, sheet=r.sheet, raw_column=col))
            used.setdefault(s["table"], set()).add(col)

    conversions = state.get("conversions")
    if conversions is None:  # a state from before the conversions step
        notes.append(CONVERSIONS_NOTE)
        conversions = []

    kept_tables = {explore.table_key(t.file, t.sheet) for n in used for t in groups[n].tables}
    skipped = {k: v for k, v in (state.get("skipped_rows") or {}).items() if k in kept_tables and v}
    plan = SalesLinePlan(
        line_tables=state.get("line_tables") or [],
        columns=columns,
        missing_columns=list(state.get("missing_columns") or {}),
        joins=joins,
        forward_fill_columns=state.get("forward_fill_columns") or [],
        statuses=state.get("statuses") or [],
        blank_status=state.get("blank_status"),
        conversions=conversions,
        return_lines=bool(state.get("return_lines")),
        service_fee_names=state.get("service_fee_names") or [],
        non_revenue_names=state.get("non_revenue_names") or [],
        subtotal_labels=(state.get("subtotal_labels") or {}).get(line_name, []) if line_name else [],
        skipped_rows=skipped,
        header_rows=dict(state.get("header_rows") or {}),
        extra_attributes=[],
        unmapped=unmapped(shop_dir, groups, used),
        notes=notes,
    )
    info["used"] = {n: sorted(c) for n, c in used.items()}
    return plan, info


def write_plan(state: WorkflowState) -> dict:
    """The parent graph's node."""
    start = time.perf_counter()
    record = {"step": "write_plan"}
    if not state.get("line_tables"):
        record.update(error="no line tables (step 1 chose none): no plan", seconds=since(start))
        return {"plan": None, "plan_problems": [], "trace": [record]}
    plan, info = build_plan(state)
    try:
        problems = check_plan(Path(state["shop_dir"]), plan, state.get("header_rows"),
                              state.get("skipped_rows") or {})
    except Exception as e:  # noqa: BLE001 - a crash of the guard is recorded, the plan is still kept
        problems = [f"plan_check crashed: {type(e).__name__}: {e}"]
    record.update(info, problems=problems, joins=len(plan.joins), columns=len(plan.columns),
                  unmapped=plan.unmapped, conversions=[c.kind for c in plan.conversions], seconds=since(start))
    return {"plan": plan.model_dump(), "plan_problems": problems, "trace": [record]}
