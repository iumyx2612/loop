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

# The shared_recheck node (agreed 2026-11-11, user: build straight into the graph): a table column still holding two
# fields after the retries and llm_check (dev_014: customer_name and customer_group both on 'Khách', the 9B kept it
# through 2 retries) is checked one field at a time, a one-sentence reason before a yes / no, the pattern that worked
# in step 7's pass 2, open_fields and step 5's blank call. {rows}, {field}, {meaning}, {column}, {readme}, {profile}
SHARED_FIELD = """A shop's sales-line table (one row per product sold in an order) has {rows:,} rows. Several of our \
sales_lines columns were matched to the same table column, but each of ours needs its own table column. Check one \
of them:
- {field}: {meaning}

The table column, with what the README says about it and what it holds over all {rows:,} rows:
- {column} (README: {readme}): {profile}
    other values: {others}

# Question
Does '{column}' hold {field}? First write one short sentence why (reason), then yes or no (holds)."""
# {others} (option 1, agreed 2026-11-11): values spread over the column's other distinct values, not the most common.
# Why: on dev_014 the most common values of 'Khách' are walk-in aliases ('Khách lẻ' ×695, 'kl', 'lẻ', 'khách lẻ'), so
# the 9B answered customer_name "no" ("generic terms … instead of specific customer names") though most of its 2,934
# distinct values are names. Only in this prompt: llm_check and open_fields keep their profile.

# The open_fields node (pass 2, agreed 2026-11-11): one fresh call per open field that code found candidate columns
# for, asked alone, a one-sentence reason before the answer. Word for word as tested on the 9B (zalo_main.py --pass2,
# 3 calls per field on the public test: every final answer right). Why alone: in llm_check's 17-field review the 9B
# never linked `status` to dev_011's 'Ghi chú' (29 return notes), even with the unused columns profiled in the
# prompt; alone, it did on dev_011 and dev_014, 3 of 3. {rows}, {field}, {meaning}, {candidates}, {not_here}
OPEN_FIELD = """A shop's sales-line table (one row per product sold in an order) has {rows:,} rows. Its columns were \
matched to our sales_lines columns; one of ours is still open:
- {field}: {meaning}

The table columns not matched to any of our columns that could hold it, each with what the README says about it and \
what it holds over all {rows:,} rows:
{candidates}

# Question
Does one of these table columns hold {field}? First write one short sentence why (reason), then the column \
(column), or "{not_here}" when none of them holds it."""
OPEN_FIELD_CANDIDATE = "- {column} (README: {readme}): {profile}"
OPEN_FIELD_NO_README = "not described"

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
