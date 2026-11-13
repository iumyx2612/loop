"""Step 3's prompt (nodes.table_snapshot): one call per table, the same question as step 2 on that table. Used by
the snapshot node (only the attached tables)."""

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

# The snapshot's one retry, when two answers point to the same column of the table (nodes.table_snapshot,
# checks.check_shared_columns; 2026-11-11, biz_109 on the 9B: customer_group and customer_code both on 'Mã KH'). A
# fresh, self-contained call like step 2's RETRY: the table with the rows that tell a shared column from its
# look-alikes ({table_note}), the problems (answers one per line, then the explanation), only the fields in question.
# {linked}: the columns known and not asked (nodes.known_columns): the columns this table was joined by, and the
# filled answers of fields related to the asked ones (same anchor family; 2026-11-11, biz_109: customer_group moved
# onto customer_name's 'Khách hàng' when only the join column was listed).
OTHER_TABLE_RETRY = """Below is one table of a shop's data. It is not the sales-line table (one row per product \
sold): it may be a list of orders, products, customers, or something else.

# What the README says about these columns
{readme_meanings}

# The table: {table_note} ({rows:,} rows below the header{tables_note})
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}
{linked}
# Your earlier answers that failed
We checked your answers, and these failed:
{problems}

# Question
Look at the table and the README meanings again, and answer these sales_lines columns again. Answer with the \
table column's name exactly as written, or "{not_here}" when this table does not have it.
{columns}"""
