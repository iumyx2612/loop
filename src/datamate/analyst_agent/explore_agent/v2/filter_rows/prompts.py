"""The filter step's prompt (nodes.filter_rows): one call per table group with candidate rows. Target model ≤ 9B:
one verdict per group of look-alike rows, two choices."""

DATA_ROW = "data row"
SUMMARY_ROW = "summary or note row"

# {readme_meanings}: column_mapping.nodes.readme_meanings_text; {normal}, {groups}: nodes.normal_text, groups_text
FILTER_ROWS = """Below is one table of a shop's data, with the README's meanings of its columns.

# Column meanings (README)
{readme_meanings}

# Table {table} ({rows:,} rows below the header)
Normal rows (text cells in quotes, blank cells empty):
{normal}

# Rows that look different
Some rows are blank where the normal rows are filled. They are put in groups of rows that look alike. Each \
example row is marked with ">" and shown with the row above and below it.

{groups}

# Question
For each group: are its rows data rows, or summary / note rows?
- "{data_row}": a real record of this table (a sale, an order, a product, a customer, ...), even if some of its \
cells are blank.
- "{summary_row}": not a record: a total or subtotal of the rows above it (per day, per month, ...), a title, a \
repeated header, a note or a comment.
Answer every group."""
