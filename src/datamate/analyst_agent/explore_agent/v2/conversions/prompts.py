"""Prompts of the conversions step (explore_agent.md, "Conversions")."""

# Part 1's fallback (agreed 2026-11-12, user): no README list the regex reads -> does the README's last section write
# conversions itself? Only the last section (user: the conversions are in the README's last part; true on all 44
# shops, survey/conversions.md). "only names the file" because 17 READMEs say where the conversion file is
# ("hệ số quy đổi ở `DanhSachQuyDoi.xlsx`") without any factor.
# Asked one line at a time (the user's multi-step README decompose, 2026-11-12): asked the whole section at once,
# the 9B answered "no" for dev_004, whose "Hàng theo bó" line writes "1 bó = 10 cây" right below a line naming
# DanhMuc.xlsx (its reason: "without providing the conversion numbers"); the pipes are in no file. A shop may have
# both a file and a README conversion, so each line is judged on its own. {line}: one of nodes.section_lines
README_LINE = """A shop's README describes its sales files. Below is one line of it.

Some shops sell a product in several units: a big unit holds a fixed number of base units (e.g. 1 thùng = 24 lon). \
A shop either writes these conversions in the README itself, with their numbers, or keeps them in a file or table; \
then the README only names that file.

--- README line ---
{line}
--- end ---

Question (writes): does this line itself write such a conversion with its number (one big unit = how many base \
units, for a product it names)? Answer "no" when it only says in which file or table the conversions are, or when \
it has no conversion.
First write one short sentence why (reason), then the answer (writes)."""
README_WRITES_REASON = "one short sentence: why, for writes"

# Part 2 (agreed 2026-11-12): which table holds the conversions, one relative choice over every table group that is
# not the line tables (not only the unattached ones: dev_013's conversions are a column of its products catalog), +
# "none"; only when part 1's regex found no README list (no surveyed shop has both). The README's last section is
# shown because it names the conversion file in 17 shops. "only gives each product's unit" / "price per unit": a
# products table with ĐVT and Giá bán, and the code-keyed tables' price column, look like conversions.
# {section}: nodes.last_section; {options}: nodes.table_options_text; {none}: NONE
TABLE_CHOICE = """A shop sells some products in several units: a big unit holds a fixed number of base units (e.g. \
1 thùng = 24 lon). Below are the last section of the shop's README and the shop's data tables other than its sales \
lines. Tables that share one header (month sheets, split files) are shown together as one option.

# README, last section
{section}

# Options
{options}

# Question (option)
Which option says, for the products it names, how many base units are in one big unit (a row per product and big \
unit with the number, or a column with text like "1 thùng = 24 lon")?
- Not a table that only gives each product's own unit, and not a price list: a price per unit is not a conversion.
- Not stock, imports, payments, deliveries or sales.
- If no option does, answer "{none}".
First write one short sentence why (reason), then the option's name exactly as written after "Option:" (option)."""
TABLE_CHOICE_REASON = "one short sentence: why, for option"
NONE = "none"

# Part 3 (agreed 2026-11-12): the chosen table's four columns (the references read product, unit, factor; base unit
# only in dev_002, no products file). key and factor_format are code's (values), so they are not asked. The price
# warning: the code-keyed tables have a price per big unit next to the factor (Giá bán, GiaDonVi).
# {name}, {rows}, {meanings}, {table}: nodes; {in_cell}, {not_here}: IN_CELL, NOT_IN_TABLE
TABLE_COLUMNS = """A shop sells some products in several units: a big unit holds a fixed number of base units (e.g. \
1 thùng = 24 lon). The table below says, for the products it names, how many base units are in one big unit.

# Table {name} ({rows} rows below the header)
README meanings of its columns:
{meanings}

{table}

# Question
Which column holds each of these?
- product_column: the product (its code or its name).
- unit_column: the big unit (thùng, hộp, lốc, ...). "{in_cell}" when the big unit is written inside the conversion \
cell (e.g. "1 thùng = 24 lon").
- factor_column: the conversion: how many base units in one big unit (a number such as 24, or text such as \
"1 thùng = 24 lon"). Never a price: a price per unit is money, not a count.
- base_unit_column: the product's base unit (lon, chai, viên, ...). "{not_here}" when no column holds it.
First write one short sentence why (reason), then the four columns."""
TABLE_COLUMNS_REASON = "one short sentence: why, for the four columns"

# Part 4 (agreed 2026-11-12): the README lines part 1 answered yes for, read into rows, one call. The rows list is
# capped (MAX_ROWS in nodes: an open-length list was padded by the 27B); `text` is not asked (code takes the lines
# naming the rows' products). "not those in a file": a yes line may also name a file. {lines}: the yes lines
README_ROWS = """A shop sells some products in several units: a big unit holds a fixed number of base units (e.g. \
1 thùng = 24 lon). Below are lines of the shop's README that write some of these conversions.

--- README lines ---
{lines}
--- end ---

Question (rows): list every conversion this text writes, one row per product and big unit:
- product: the product's name exactly as the text writes it (one product per row; a sentence naming three products \
gives three rows)
- unit: the big unit
- factor: how many base units are in one big unit (a number)
- base_unit: the base unit, or null when the text does not say
Only conversions written in this text with their numbers, not the ones kept in a file or table it names.
First write one short sentence why (reason), then the rows."""
README_ROWS_REASON = "one short sentence: why, for rows"
IN_CELL = "(inside the conversion cell)"
NOT_IN_TABLE = "(not in this table)"
