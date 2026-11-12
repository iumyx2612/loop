# The explore_agent: the first step of the analyst_agent. It takes one shop folder it has never seen
# (<split>/data/<shop>/: README.md + xlsx / csv / sqlite files) and returns a SalesLinePlan (state.py): which tables
# hold the sales lines, where every sales_lines column comes from, how the other tables join on, the status words,
# and the inputs of the converter's fixed cleaning. It only reads files; write_code turns the plan into a converter.
#
# It is a fixed workflow (v2/, design and decisions in explore_agent.md at the project root): small steps where code
# fetches exactly the data a step needs, the LLM answers one small enum question, and code checks the answer.
# The v1 agent loop (think <-> search -> write_plan) was removed on 2026-11-11.
#
# Code layout:
# - this folder, shared by the workflow: explore.py (tables, groups, cells: the read helpers), readme.py (README
#   column lists), plan_check.py (the plan's guard, in code), state.py (SalesLinePlan, ExploreContext, ShopState),
#   nodes.py (the fixed steps confirm_headers, prepare), prompts.py (HEADER), utils.py (rulebook sections, trace
#   helpers)
# - v2/: the workflow's graph (v2/agent.py) and one folder per step
#
# Model constraint (competition rule): all models at inference total <= 12B parameters (LLM target ~9B). Keep each
# step simple: few choices, short replies, and checks in code that say exactly what is wrong.
