"""The explore_agent v1 graph: the shared fixed steps, then a think ⇄ search loop (built like src/impls/qna), then
the plan is written from think's notes and checked in code:

    START → confirm_headers → prepare → think ⇄ search
                                          │ done   ↑
                                          ↓        │ problems (at most MAX_PLAN_ATTEMPTS plans)
                                     write_plan → plan_check → END (passed, or out of attempts)

Where each node comes from:
- confirm_headers: shared (explore_agent/nodes.py)
- prepare, search, plan_check: nodes.py here (prepare = the shared one + the loop's starting values; search runs
  the search subgraph tool/ inside a node, because the subgraph has its own state, a message list)
- think: think/        write_plan: write_plan/
think, search, write_plan and plan_check route themselves with Command(goto=...), so only
START -> confirm_headers -> prepare -> think are edges.
"""
from langgraph.graph import START, StateGraph

from src.datamate.analyst_agent.explore_agent.nodes import confirm_headers
from src.datamate.analyst_agent.explore_agent.state import ExploreContext
from src.datamate.analyst_agent.explore_agent.v1.nodes import plan_check, prepare, search
from src.datamate.analyst_agent.explore_agent.v1.state import ExploreAgentState
from src.datamate.analyst_agent.explore_agent.v1.think import think
from src.datamate.analyst_agent.explore_agent.v1.utils import MAX_BAD_REPLIES, MAX_PLAN_ATTEMPTS, MAX_ROUNDS
from src.datamate.analyst_agent.explore_agent.v1.write_plan import write_plan

# Graph steps (pass as config={"recursion_limit": ...}): think + search per round, think + write_plan +
# plan_check per plan, plus the fixed steps; LangGraph's default of 25 would cut a long exploration
RECURSION_LIMIT = 2 * MAX_ROUNDS + 3 * MAX_PLAN_ATTEMPTS + MAX_BAD_REPLIES + 10


def build_graph():
    g = StateGraph(ExploreAgentState, context_schema=ExploreContext)
    g.add_node("confirm_headers", confirm_headers)
    g.add_node("prepare", prepare)
    g.add_node("think", think)
    g.add_node("search", search)
    g.add_node("write_plan", write_plan)
    g.add_node("plan_check", plan_check)
    g.add_edge(START, "confirm_headers")
    g.add_edge("confirm_headers", "prepare")
    g.add_edge("prepare", "think")
    return g.compile()


explore_agent = build_graph()
