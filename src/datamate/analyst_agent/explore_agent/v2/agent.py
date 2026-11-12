"""The explore_agent v2 graphs: the shared fixed steps, then the workflow, one node per step (explore_agent.md).

    explore_agent         START → confirm_headers → prepare → filter_rows → choose_line_tables → map_line_columns
                                → find_anchors → attach → resolve_anchors → snapshot → report_missing
                                → map_statuses → map_line_kinds → cleaning_inputs → map_conversions → write_plan
                                → END
    filter_graph         START → confirm_headers → prepare → filter_rows → END                   (up to step 0)
    sales_lines_graph     START → confirm_headers → prepare → filter_rows → choose_line_tables → END  (up to step 1)
    column_mapping_graph  START → map_line_columns → END                                          (step 2 only)
    find_anchors_graph    START → find_anchors → END                                              (step 3's first part)
    attach_graph          START → attach → END                                                    (step 3, no LLM)
    anchors_graph         START → attach → resolve_anchors → END           (step 3 after find_anchors, on a saved state)
    snapshot_graph        START → snapshot → END                           (step 3 after resolve_anchors, on a saved state)
    missing_columns_graph START → report_missing → END             (step 4 after snapshot, on a saved state, no LLM)
    statuses_graph        START → map_statuses → END               (step 5 after snapshot, on a saved state)
    filter_statuses_graph START → filter_rows → map_statuses → END (step 5 on a state saved before the filter step)
    line_kinds_graph      START → map_line_kinds → END             (step 7 after map_statuses, on a saved state)
    cleaning_inputs_graph START → cleaning_inputs → END            (step 6a-6c after map_line_kinds, saved state, no LLM;
                                                                    on a state without step 7, 6c counts every row)
    conversions_graph     START → map_conversions → END            (conversions, on any saved state after prepare)
    write_plan_graph      START → write_plan → END                 (step 8 after cleaning_inputs, saved state, no LLM)

The partial graphs let one step be run on its own: run sales_lines_graph once, save the state
(v2/utils.dump_state), then run column_mapping_graph on the saved state as often as needed, without redoing
step 1. All share WorkflowState, so a saved state fits any of them. Each later step adds one node.

Where each node comes from: confirm_headers, prepare: shared (explore_agent/nodes.py); filter_rows: filter_rows/
(step 0); choose_line_tables: find_line_tables/; map_line_columns: column_mapping/ (its retry loop runs inside the
node); find_anchors, attach, resolve_anchors, snapshot: fill_missing_schema/; report_missing: missing_columns/;
map_statuses: status_words/; cleaning_inputs: cleaning_inputs/; map_line_kinds: line_kinds/; map_conversions:
conversions/; write_plan: write_plan/.
"""
from langgraph.graph import END, START, StateGraph

from src.datamate.analyst_agent.explore_agent.nodes import confirm_headers, prepare
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.v2.planner.cleaning_inputs import cleaning_inputs
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping import map_line_columns
from src.datamate.analyst_agent.explore_agent.v2.planner.conversions import map_conversions
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.anchor_resolve import resolve_anchors
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.attach import attach
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.find_anchors import find_anchors
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.snapshot import snapshot
from src.datamate.analyst_agent.explore_agent.v2.planner.filter_rows import filter_rows
from src.datamate.analyst_agent.explore_agent.v2.planner.find_line_tables import choose_line_tables
from src.datamate.analyst_agent.explore_agent.v2.planner.line_kinds import map_line_kinds
from src.datamate.analyst_agent.explore_agent.v2.planner.missing_columns import report_missing
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.analyst_agent.explore_agent.v2.planner.status_words import map_statuses
from src.datamate.analyst_agent.explore_agent.v2.planner.write_plan import write_plan

NODES = {"confirm_headers": confirm_headers, "prepare": prepare, "filter_rows": filter_rows,
         "choose_line_tables": choose_line_tables,
         "map_line_columns": map_line_columns, "find_anchors": find_anchors, "attach": attach,
         "resolve_anchors": resolve_anchors, "snapshot": snapshot, "report_missing": report_missing,
         "map_statuses": map_statuses, "map_line_kinds": map_line_kinds,
         "cleaning_inputs": cleaning_inputs, "map_conversions": map_conversions, "write_plan": write_plan}


def build_graph(steps: list[str]):
    """A graph running `steps` (names in NODES) one after the other."""
    g = StateGraph(WorkflowState, context_schema=ExploreContext)
    for name in steps:
        g.add_node(name, NODES[name])
    for a, b in zip([START, *steps], [*steps, END]):
        g.add_edge(a, b)
    return g.compile()


explore_agent = build_graph(["confirm_headers", "prepare", "filter_rows", "choose_line_tables", "map_line_columns",
                              "find_anchors", "attach", "resolve_anchors", "snapshot", "report_missing",
                              "map_statuses", "map_line_kinds", "cleaning_inputs", "map_conversions", "write_plan"])
filter_graph = build_graph(["confirm_headers", "prepare", "filter_rows"])
sales_lines_graph = build_graph(["confirm_headers", "prepare", "filter_rows", "choose_line_tables"])
column_mapping_graph = build_graph(["map_line_columns"])
find_anchors_graph = build_graph(["find_anchors"])
attach_graph = build_graph(["attach"])
anchors_graph = build_graph(["attach", "resolve_anchors"])
snapshot_graph = build_graph(["snapshot"])
missing_columns_graph = build_graph(["report_missing"])
statuses_graph = build_graph(["map_statuses"])
filter_statuses_graph = build_graph(["filter_rows", "map_statuses"])
line_kinds_graph = build_graph(["map_line_kinds"])
cleaning_inputs_graph = build_graph(["cleaning_inputs"])
conversions_graph = build_graph(["map_conversions"])
write_plan_graph = build_graph(["write_plan"])
