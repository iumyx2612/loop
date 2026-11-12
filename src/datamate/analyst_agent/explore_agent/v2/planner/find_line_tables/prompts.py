"""Step 1's prompt (nodes.choose_line_tables). Target model ≤ 9B, so it asks one thing."""

# {options}: nodes.options_text
LINE_TABLES = """Below are a shop's README and its data tables. Tables that share one header (month sheets, split \
files) are shown together as one option.

# README
{readme}

# Options
{options}

# Question
Which option holds the shop's sales lines?
A sales line is one product sold in an order: it names a product and has a quantity, and usually a price or an \
amount. One order can have several lines.
- Choose the table of product lines, not an orders table with one row per order (order totals, payments), even if \
that table also has amounts.
- Only if the shop has no product lines at all, choose the table with one row per sale or order.
- Never a product list, customer list, import, stock, debt, delivery, price list or unit conversion table.
Answer with the option's name exactly as written after "Option:"."""
