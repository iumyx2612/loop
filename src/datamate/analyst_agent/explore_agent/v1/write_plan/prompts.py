"""Prompts of the write_plan node. SCHEMA is shared (explore_agent/prompts.py), REPLY_PROBLEM is in v1/prompts.py.
Its output schema is the shared SalesLinePlan (explore_agent/state.py)."""

WRITE_PLAN_SYSTEM = """You write the plan of a data conversion system. A shop keeps its sales in its own files \
(Excel, csv, sqlite) with its own column names. An exploring agent has looked at the files and written notes. \
From the README, the file list and those notes, fill in the plan: where every column of our standard table \
`sales_lines` comes from (file, sheet, column), how the tables join, the status words, and the cleaning inputs. \
Another step writes the converter from your plan, so use file, sheet and column names exactly as in the file list \
and the notes. Do not guess what the notes do not say: a column the shop does not record goes in missing_columns.

The cleaning of the line table is done by fixed code: repeated header rows, subtotal rows matching common \
labels, forward fill of the columns you list, and duplicate lines. You only name the inputs.

# The sales_lines table
{schema}

# Rules from the rulebook (how the files are written and what counts)
{rulebook}"""

WRITE_PLAN_USER = """Shop {shop_id}.

# README.md
{readme}

# Files and tables
Row numbers are the rows of the file (row 1 = first row). Tables with the same header are listed together.
{files}

# The exploring agent's notes
{notes}
{problems}
Write the whole plan."""

PLAN_PROBLEMS = """
# Your last plan failed the check
Fix these problems and keep the rest of the plan:
{problems}

Your last plan:
{plan}
"""
