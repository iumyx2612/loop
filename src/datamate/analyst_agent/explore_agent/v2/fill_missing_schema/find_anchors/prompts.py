"""find_anchors' prompt (nodes.table_anchors): one call per non-line table, which of its columns are anchors."""

# {readme_meanings}, {rows}, {tables_note}, {table}, {anchors}, {not_here}: nodes.anchors_message
TABLE_ANCHORS = """Below is one table of a shop's data. It is not the sales-line table (one row per product sold). \
We want to know which of its columns hold the codes or names that identify an order, a product or a customer, so \
the table can be linked to the shop's other tables.

# What the README says about these columns
{readme_meanings}

# The table: its columns and first rows ({rows:,} rows below the header{tables_note})
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}

# Question
For each item below, which column of this table holds it?
- Answer with the table column's name exactly as written, or "{not_here}" when this table has no such column.
- A column counts when it holds the codes or names themselves, also when the table only refers to them (e.g. a \
customer code on an order row).

{anchors}"""
