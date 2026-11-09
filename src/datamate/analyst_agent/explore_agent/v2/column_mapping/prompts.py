"""Step 2's prompts (nodes.py): the question, the two follow-up messages of the retry, and the llm_check review."""

# What each asked sales_lines column means, in the order they are asked (plan_check.MAPPABLE, then OPTIONAL
# without line_product_name: nodes.ASKED). Shown in the prompt and used as each reply field's description.
COLUMN_MEANINGS = {
    "order_id": "order / invoice / bill code",
    "order_date": "the day the order was placed",
    "order_time": "the time the order was placed or created (may be the same column as order_date when it holds "
                  "date and time)",
    "status": "order status (completed, cancelled, returned, in delivery ...); in some books it is in a note "
              "column",
    "customer_name": "customer name as written on the order",
    "customer_group": "the group the shop puts the customer in (lẻ / thân thiết / sỉ / VIP ...); not the customer "
                      "type (cá nhân / công ty)",
    "channel": "sales channel (store, Zalo OA, phone, e-commerce)",
    "payment_method": "payment method (cash, transfer, e-wallet, COD, debt)",
    "seller": "staff member who sold (code or name)",
    "product_code": "product code",
    "product_name": "product name",
    "product_group": "product group / category",
    "unit": "unit written on the line (may be the same column as qty when the cell holds e.g. '3 chai')",
    "qty": "quantity sold, in that unit",
    "base_unit": "the product's base (smallest) unit",
    "unit_price": "price per one unit, before discount",
    "line_amount": "amount of the line after the line discount (for a table with one row per order: the "
                   "order's amount)",
    "cost_price": "cost per unit written on the sale line",
    "qty_base": "quantity already converted to the base unit",
    "line_discount": "discount on the whole line",
    "customer_code": "customer code",
}

NOT_HERE = "(not in this file)"

# The first message. {readme_meanings}, {table}, {columns}: nodes.map_line_columns
LINE_COLUMNS = """Below is a shop's sales-line table: one row per product sold in an order. It may be split over \
several sheets or files; they all have these columns.

# What the README says about these columns
{readme_meanings}

# The table: its columns and first rows
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}

# Question
For each sales_lines column below, which column of this table holds it?
- Answer with the table column's name exactly as written, or "{not_here}" when this table does not have it. \
Only this table counts: other files are looked at later, so a value that is only in another file is \
"{not_here}".
- The same table column may be the answer for several sales_lines columns, e.g. a quantity cell like '3 chai' \
holds both qty and unit; a cell with date and time holds both order_date and order_time.

{columns}"""

# After a reply whose answers failed the checks (checks.py): a fresh, self-contained call, not a message added to the
# conversation (user, 2026-11-07; test.py run 7 on dev_003). It repeats the README meanings and the table, adds the
# rows that tell a shared column from its look-alikes ({table_note}: nodes.retry_message), lists the problems
# (each: the failed answers one per line, then the explanation) and asks only {columns}, the fields in question.
RETRY = """Below is a shop's sales-line table: one row per product sold in an order. It may be split over \
several sheets or files; they all have these columns.

# What the README says about these columns
{readme_meanings}

# The table: {table_note}
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}

# Your earlier answers that failed
We checked your answers on every row of the table, and these failed:
{problems}

# Question
Look at the table and the README meanings again, and answer these sales_lines columns again. Answer with the \
table column's name exactly as written, or "{not_here}" when this table does not have it.
{columns}"""

# After a reply that did not fit the output format: added to the request that got it, after that reply
REPLY_PROBLEM = """Your last reply could not be read ({error}). Reply in the output format, with every column \
asked."""

# The llm_check node: one fresh call (no conversation) that reviews the final matching of the fields code cannot
# check the meaning of. {locked}, {review}, {problems}: nodes.llm_check
LLM_CHECK = """Below is a shop's sales-line table and how its columns were matched to our sales_lines columns. \
Review the matching once.

# What the README says about these columns
{readme_meanings}

# The table: its columns and first rows
Text cells are in quotes; <datetime …> is a real date cell; an empty cell is blank.
{table}

# Already checked on every row (not to review)
{locked}

# The matching to review
Each item: our column ← the table's column (what our column means), then what that table column holds over all \
{rows:,} rows of the table.
{review}
{problems}
# Question
Does each table column hold what our column means? Judge from the values over all rows, not only the first rows: \
e.g. a column with one value on every row cannot be the staff member who sold each line. A column matched \
"{not_here}" may have a table column that holds it.
For each of our columns under "The matching to review", give the table column that holds it, or "{not_here}". \
Keep the current matching where it is right."""
