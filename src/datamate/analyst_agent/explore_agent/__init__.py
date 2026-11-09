# This agent is the first step of the analyst_agent: it always runs first when the analyst_agent is given a shop
# It's duty:
# - Take one shop folder (<split>/data/<shop>/: README.md + xlsx / csv / sqlite files) it has never seen before
# - Find out where every sales_lines column comes from: which file / sheet / column, and what the raw values look like
# - Know the shared sales_lines schema (its system prompt holds the schema table and rulebook §1, §3, §6, §8), so it
#   puts every raw column in the right schema column, across all the shop's files (line file + orders / products /
#   customers / conversion files joined by key), and reports what fits no column instead of dropping it
# - Do NOT write or run code, do NOT clean or convert anything: only LOOK at the files through its read-only tools
# - Have its own prompt, its own context and its own tools. Its tool calls and their outputs stay inside it;
#   the analyst_agent only receives its final result
#
# Fixed steps before the agent (no tool calls needed for these):
# - confirm_headers: for xlsx sheets the README does not describe, a small LLM call picks the header row
# - prepare: the README and the file list (every file, sheet / sqlite table, its header row and columns, and its
#   real row count, so peek can reach any part), shown in think's and write_plan's prompts
#
# Code layout (refactored 2026-11-03 after the LangGraph project layout: agent.py builds the graph, nodes.py,
# state.py, tools.py, prompts.py beside it):
# - this folder: what every version shares: explore.py (the tools as plain functions), readme.py, plan_check.py,
#   state.py (SalesLinePlan, ExploreContext, ShopState), nodes.py (the fixed steps confirm_headers, prepare),
#   prompts.py (HEADER, SCHEMA), utils.py (rulebook sections, trace helpers)
# - v1/: the agent loop below (graph in v1/agent.py); v1/think = the think node, v1/write_plan = the write_plan
#   node, v1/tool = the search subgraph and its tools
# - the fixed workflow that replaces v1 (explore_agent.md) gets its own folder
#
# The v1 agent (graph in v1/agent.py): think ⇄ search, then write_plan → plan_check
# - think: each round turns the search report into notes and asks for one more thing, or says it is done
# - search: a fresh small ReAct loop over the three tools per request; its tool calls + outputs are the report
# - write_plan: fills in the plan from the README, the file list and think's notes (no tools)
# - plan_check: checks the plan in code; problems go back to think (look again, or say how to fix) -> write_plan
#
# Tools (read-only, small text outputs, the shop folder and header rows are injected, never typed by the LLM):
# - peek(file, sheet, start, n): n raw rows from row `start`, cells as stored, no parsing
#   (header offset, title / filter rows, two-level headers, forward-fill gaps, footers at the end of a sheet)
# - column_values(file, sheet, column): distinct values with counts (when ≤ 30) or the mix of formats with
#   examples (money, dates, quantities); every raw status / unit / channel / payment word is found here
# - find_rows(file, sheet, text): the rows holding some text, with their row numbers
#   (subtotal rows like "Tổng ngày" / "Cộng", "Phí phục vụ" lines, voucher / ship rows, return lines)
#
# Output: the structured plan for the shop (state.SalesLinePlan), written by write_plan from think's notes
# - which tables hold the sales lines, which file / sheet / column feeds each sales_lines column, and which columns
#   the shop does not record
# - how the other tables join on (orders -> lines by order code, products -> lines by product code,
#   customers -> orders by customer code, ...)
# - the inputs of the fixed cleaning: the order-id column (from the column mapping) and the forward-fill columns
# - every raw status word -> one of the 4 standard statuses (completed / cancelled / returned / in_delivery)
# - where the unit conversions come from (a column on the line, a conversion file, the README list, or none)
# - what needs shop-specific code: return lines, service-fee lines, non-revenue rows, subtotal labels, extra product
#   attributes (brand, size, ...), data that fits no schema column, and free notes (toppings in the drink's cell)
# - The plan is checked in code before it is handed on: every schema column is mapped or marked missing, every
#   raw status word maps to a known status, every file / sheet / column named exists, joins link rows, the
#   forward-fill columns really are blank on continuation lines. A failed plan goes back to the explore_agent
# - Then write_code gets the plan (not the exploration messages) and writes the shop's converter
#
# Not the agent's job (fixed code, no decision left):
# - header offset: confirm_headers / the README, before the agent
# - the cleaning of the line table, in a fixed order (common.clean_line_table): repeated header rows -> subtotal
#   rows -> forward fill -> duplicate lines within an order. The agent only supplies its inputs (above)
#
# Why the explore_agent also makes the plan (decided 2026-11-01), and why inside it think and write_plan are split
# (2026-11-02):
# - The plan stays inside the explore_agent: a findings report handed to another agent would miss things we can't
#   yet define, and the planning step could not look again. Here a failed plan goes back to think, which can
# - think used to return the plan itself. On Sonnet (biz_108, two runs) it twice wrote "done" notes and left both
#   request and plan empty: it treats "done" as its own step. So think now only explores (notes + request, empty =
#   done) and write_plan writes the plan from the notes. A side gain for the 9B model: the plan's ~6k-char schema
#   no longer rides along with every exploring round
#
# Model constraint (competition rule): every task runs on a 9B LLM. Keep each step simple:
# few tools with few parameters, short tool outputs, and checks in code that say exactly what is wrong
