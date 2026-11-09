"""Step 3's prompt (nodes.table_snapshot): one call per table, the same question as step 2 on that table. Used by
the trial (fill_from_tables: every non-line table) and by the snapshot node (only the attached tables)."""

# {readme_meanings}, {rows}, {tables_note}, {table}, {columns}, {not_here}: nodes.table_message
OTHER_TABLE = """Below is one table of a shop's data. It is not the sales-line table (one row per product sold): it \
may be a list of orders, products, customers, or something else.

# What the README says about these columns
{readme_meanings}

# The table: its columns and first rows ({rows:,} rows below the header{tables_note})
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}

# Question
For each sales_lines column below, which column of this table holds it?
- Answer with the table column's name exactly as written, or "{not_here}" when this table does not have it.
- The same table column may be the answer for several sales_lines columns.

{columns}"""
