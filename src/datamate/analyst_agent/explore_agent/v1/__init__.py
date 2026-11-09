"""explore_agent v1: the agent loop (think ⇄ search → write_plan → plan_check), tried on Sonnet and the 27B.
Replaced by the fixed workflow (explore_agent.md); kept as the base of agent mode.
- agent.py: the graph (starts with the shared fixed steps confirm_headers, prepare); nodes.py: prepare, search,
  plan_check; state.py: ExploreAgentState; utils.py / prompts.py: what think and write_plan share
- think/: the think node (notes + one request per round)
- tool/: the search subgraph, a ReAct loop over peek / column_values / find_rows
- write_plan/: the write_plan node (the whole SalesLinePlan from think's notes)
"""
from src.datamate.analyst_agent.explore_agent.v1.agent import RECURSION_LIMIT, explore_agent
from src.datamate.analyst_agent.explore_agent.v1.think.nodes import think_system
from src.datamate.analyst_agent.explore_agent.v1.write_plan.nodes import write_plan_system

__all__ = ["RECURSION_LIMIT", "explore_agent", "think_system", "write_plan_system"]
