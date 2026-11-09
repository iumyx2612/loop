# explore_agent: a fixed workflow that writes a shop's SalesLinePlan

Handoff for whoever works on the explore_agent. **Self-contained**: read it alone to work on the explore_agent.
`problem.md` (project root) is the whole project's history; open it only for something outside the explore_agent.
This file holds decisions, designs and their reasons; run results go in the reply, never here.

Order: project context → workflow-wide decisions → the graph and each step (0–7) → facts → runners → code layout →
working rules → environment.

## Project context
### The task
ZaloAI "DataMate": an LLM agent answers Vietnamese business questions about small shops' sales data. One
question = `{"id", "business_id", "question", "as_of_date"}`; the shop's folder `dataset/zaloAI/<split>/data/<shop>/`
holds xlsx / csv / sqlite files and a `README.md` describing them (READMEs differ per shop; lists may be split
across files `_1`, `_2`, `_2025` or month sheets). `<split>/rulebook.md` defines every term (revenue, cancelled,
periods, ranking, rounding, unanswerable) and the answer format; the public-test rulebook differs from train
(industry rules). Scoring: +1 right, −1 wrong, 0 abstain.
- Splits: `train` (30 shops `biz_101`–`biz_130`, with `answers.jsonl`), `public_test` (14 shops `dev_001`–`dev_014`,
  scored only on the website). The final test has **unseen shops**.
- **Competition rule: every task runs on an LLM of at most 9B parameters** (model not chosen yet). Development is
  on Qwen3.8-27B (the user's server); every step must stay simple enough for ≤ 9B.

### The two agents
- **query_agent** (`src/datamate/query_agent/`, built): answers one question with tools over a shop's converted
  `sales_lines` table (`data_std/<split>/<shop>/sales_lines.parquet` + `manifest.json`). Never reads raw files.
- **analyst_agent** (`src/datamate/analyst_agent/`, in progress): turns an unseen shop folder into
  `sales_lines.parquet` + `manifest.json`, once per shop, offline. Scope: **sales_lines only** for now. Planned:
  `explore_agent → write_code → run_and_check (→ back to write_code on failure) → manifest + brief`.
  - **explore_agent** (this file): reads the README + files and produces a `SalesLinePlan`. Built: steps 0–5 (step
    3 without its field resolve); steps 6–7 to design.
  - **write_code** (not built): the LLM writes `convert(shop_dir) -> DataFrame` from the plan, calling only the
    shared helpers, with reference converters as examples. If too hard for ≤ 9B, part of plan → code may become a
    fill-in template run by fixed code. The explore_agent outputs only the plan; applying it is write_code's job.
  - **run_and_check** (not built, no LLM): runs the code in a subprocess; key-free checks (`schema.validate`, every
    status mapped, no unparsed cells, dates in range, `line_amount ≈ qty × unit_price − line_discount`, export
    subtotals match the lines). Output to `data_std_agent/<split>/<shop>/`, never over the references.
  - **Benchmark** (built): `scripts/bench_analyst.py [--split] [shop ...]` compares an agent conversion with the
    reference conversion of the same shop.

### Shop facts (survey of the 30 train shops; per-column listing in `survey/sales_lines_columns.md`)
| File type | Row = | Shops |
|---|---|---|
| sales_lines | one product line of an order | 29/30 (not 110) |
| orders (separate file) | one order: total, paid, debt, channel, payment, seller, time | 106, 108, 109, 110, 111, 119, 120, 123, 124, 127, 128, 129; elsewhere order fields repeat on every line |
| products | code, name, group, base unit, price, cost | 106, 108, 109, 111, 112, 115–124, 127–130 |
| unit_conversions | product, big unit, factor | file (102–106, 109, 117, 121–124) or a list in the README (101, 107, 108, 111–116, 119, 120, 125–130) |
| customers | code, name, phone, group | 106, 108, 109, 111, 119, 120, 123, 124, 127, 128, 129 (no group: 106, 119, 120, 127) |
| payments, imports, stock, price_history, lots, drugs, deliveries, debt_terms | event / other tables (**not** sales_lines sources) | payments 106, 109, 111, 123, 124, 127, 129; imports 105, 106, 108, 109, 117, 119–124, 128; stock 105, 106, 108, 109, 117, 119, 120, 124, 128; lots, drugs 119–124; deliveries, debt_terms 115–118 |

- Gaps: 110 orders only (no lines); 107, 113, 126 no order id and no customer (each row counts alone).
- Lines with code only, name from the products file: 111, 123, 127, 129. Line discount column: 106, 108, 109, 119,
  120, 123, 124, 128, 129. Cost at sale: 106, 109, 123, 124. Order time: 105, 117.
- Cleaning seen: header offsets (title rows, a filter row in 103 / 121 / 122, two-level headers 105 / 117); forward
  fill (103, 121, 122, dev_004, dev_005, dev_011: order fields on the first line only); subtotal and note rows;
  duplicate lines within an order (count once); mixed date formats (`dd/mm/yyyy`, Excel serials, `1 thg 6 2025`,
  year-less `1/1`, ISO); money as `13,353,000`, `55.000đ`, `25k`, `1tr440`, `16,56tr`, negatives; qty with unit
  inside (`3 chai`); walk-in aliases (`Khách lẻ`, `kl`, `lẻ`); phone suffixes on names; return lines with negative
  qty (105, 117, dev_004; their date is the return's, `order_date` comes from the sale lines); `Phí phục vụ`
  service-fee lines (125–130: revenue, not a product).
- Shops whose README lists no columns: train 106, 109, 123, 124; public 003, 006, 014 (106 / 109 share biz_108's POS
  layout).

### The sales_lines schema (`src/datamate/schema.py`)
One row = one product (or service-fee) line of an order, after cleaning. Order-level columns (date, time, status,
customer, channel, payment, seller) are **copied onto every line**, also when the shop has a separate orders file;
product attributes (group, base unit) are copied from the product. So no query tool ever joins.

| Column | Meaning |
|---|---|
| `order_id`, `line_no` | order code (null in 107, 113, 126); line number when recorded |
| `order_date`, `order_time` | when the order was placed (periods go by order date); return lines: from the sale lines |
| `status` | completed / cancelled / returned / in_delivery; an order with any return line is returned |
| `customer_key`, `customer_name`, `customer_group` | key = customer code if any, else normalized name; null for walk-in. Group lẻ / thân thiết / sỉ from the customers file |
| `channel`, `payment_method`, `seller` | store / zalo_oa / phone / ecommerce; cash / transfer / ewallet / cod / debt; staff code or name as written |
| `product_key`, `product_code`, `product_name`, `product_group` | key = code if any, else normalized name; name from the products file when the line has only a code |
| `line_kind`, `is_return_line` | product / service_fee; negative-qty return line |
| `unit`, `qty`, `base_unit`, `unit_factor`, `qty_base` | unit on the line (normalized); qty in it; the product's base unit; base units per unit; `qty × unit_factor` |
| `return_date` | return lines only: the return's date |
| `unit_price`, `line_discount`, `line_amount` | VND; price before discount; discount on the whole line (implied `qty × unit_price − line_amount` when no column); amount after discount = revenue unit |
| `cost_price` | cost per unit **written on the sales line**; a product list's cost is today's cost, not usable |
| `source_file`, `source_sheet`, `source_row` | debugging |

On all 29 line shops: `line_amount = qty × unit_price − line_discount` (discount per line), `unit_price` before
discount.

### The plan (`explore_agent/state.py`, `plan_check.py`)
- **`SalesLinePlan`**: `line_tables` (`TableRef` file / sheet), `columns` (`ColumnSource`: schema_column ← file /
  sheet / raw_column), `missing_columns`, `joins`, `forward_fill_columns`, `statuses` (`StatusWord` per raw word),
  `blank_status` (the status of an order whose status cell is still blank after forward fill), `conversions`
  (`column` / `file` / `readme` / `none`) + `conversion_table`, `return_lines`, `service_fee_names`,
  `non_revenue_names`, `subtotal_labels`, `extra_attributes`, `unmapped`, `notes`.
- **`Join`**: rows of `file` attach to rows of `on_file` where `key` = `on_key`; `on_file` may be a table joined
  earlier (biz_108: customers → **orders** by `Mã KH` = `Mã khách hàng`, orders → lines by `Mã hóa đơn`).
- **Column classes** (`plan_check`): `MAPPABLE` (must be mapped or listed missing; `line_no` is not in it: see step
  2), `OPTIONAL` (qty_base, line_discount, customer_code, line_product_name: may be mapped; the last two are
  working columns the converter reads but does not store), `DERIVED` (customer_key, product_key, line_kind,
  is_return_line, return_date, unit_factor, source_*: never mapped), `ALWAYS_RECORDED` (order_date, line_amount).
- **`plan_check.check_plan`** (no LLM; each problem says what to do): every column mapped or missing, names exist,
  a used file is a line table or joined, `cost_price` from a line table; joins (`on_file` attached before, key
  unique, ≥ 50 % of `on_key` values found: `MIN_JOIN_COVERAGE`); statuses (every distinct raw value listed, none
  invented, ≤ 40); forward fill (starts with the order-id column); texts exist; return_lines ↔ negative qty;
  conversions ↔ conversion_table / qty_base. `scripts/checks/check_plan_check.py` (a hand-written biz_108 plan
  passes, broken copies fail). Not in the v2 graph yet; it has its own subtotal-label handling, to align with the
  filter step when the plan is put together.
- **Values computed from several rows / columns are never part of the mapping**: the mapping only says where a raw
  value lives. Forward fill (step 6), return-line dates and the returned status (fixed code via `return_lines`),
  qty_base / implied discount (`DERIVED`), values in another file (step 3), odd structures like dev_002 toppings
  inside a drink's cell or dev_012 values inside the `Xe` cell (step 7).

### Shared code
- `src/datamate/parse.py`: `is_blank`, `clean_text` (None for blanks: never `str(v).strip()` a raw cell, it gave
  `"nan"` keys), `norm_text` (NFC, lower case, single spaces, accents kept), `fold_accents`, `parse_money`,
  `parse_qty`, `parse_date`, `parse_datetime`, `strip_phone`, `customer_name_key`, `map_status` / `STATUS_MAP`,
  `map_channel`, `map_payment`. Unknown formats raise, never guess.
- `src/datamate/reference/common.py`: `read_xlsx` / `read_csv` / `read_sqlite`, `clean_line_table` (repeated headers
  → subtotals by the fixed `SUBTOTAL` regex → forward fill → duplicates), `forward_fill` (only continuation lines,
  i.e. blank order id, only from their own order's first line), `readme_conversions`, `conversion_lookup`,
  `match_names` (accent-insensitive name join), `parse_qty_and_unit`, `money`, `dates`, `implied_discount`,
  `returned_orders_from_negative_lines`.
- `src/datamate/schema.py`: `SALES_LINES_COLUMNS`, `finalize`, `validate`, `manifest`, `write`.
- **Reference converters** (ground truth, hand-written; `scripts/convert_reference.py [--split public_test]` →
  `data_std/`): train biz_108, 116, 117, 122, 128; all 14 public-test shops (dev_002 café toppings, 003 POS with cost,
  004 book with returns, 005 pharmacy, 006 sqlite, 007 / 008 restaurant, 009 orders only, 010 CSV by codes, 011 /
  014 phone books with `Ghi tắt` abbreviations and status from Ghi chú, 012 repair tickets, 013 mẹ và bé).
- Converter work the query_agent is waiting for: dev_013 brand / size from the catalog and voucher / points /
  shipping rows as non-revenue line kinds, dev_012 vehicle model from the `Xe` cell, a paid-tickets flag.

## Workflow-wide decisions
- **A fixed workflow, not an agent loop** (user): a fixed sequence of small steps; in each step **code fetches
  exactly the data the step needs**, the LLM (if needed) answers one small constrained question, and code checks
  the answer right away. Why (user): "much safer and we can directly control the input/output". The v1 loop
  (`think ⇄ search → write_plan → plan_check`, kept in `v1/` as the base of agent mode) produced plans that passed
  `plan_check` but were wrong: one reply filling all 16 plan fields translated correct notes wrongly.
- **Agent mode only as a fallback**, for a step genuinely too flexible for a workflow (candidates: odd structures,
  step 7).
- **Output stays a `SalesLinePlan`**; the steps fill its fields part by part.
- **Each step is a node of the LangGraph graph**; retries and give-up are designed per step with the user. Each step
  has its own check and retry, so a mistake in step 2 never sends step 1 back.
- **Temperature 0** (user hosts the model). **Guard = `plan_check`**; plans are evaluated **by hand** by the user;
  no reference plans or comparison script.
- **No hard-coded values from the 44 known shops** (user): an unseen shop may write things differently. Exception
  agreed for status words (step 5: meanings, not structure). Hand-written lists (`SUBTOTAL`, `map_status`) stay as
  answer keys on train / public test.
- **Structured replies** (lessons from the 27B): a fixed object of enums (or one enum field) is safe; avoid
  optional-length lists and free text (the 27B padded a list with newlines until max_tokens). Reply fields are
  built per shop (`pydantic.create_model` with `Literal` enums): an invented name fails validation, so it is never
  accepted. `method="json_schema"`. Keep `max_tokens` small (runs use 4000): a whitespace loop then ends as an
  unreadable reply instead of a 120 s proxy timeout. Proposed, not done: make 2000 the runner default (now 10000).
- `v2/utils.call_structured`: one call + its trace record (usage, `stop_reason` / `finish_reason`, error + raw
  reply on a parse failure; the OpenAI client's `LengthFinishReasonError` becomes an unreadable reply).

## The graph (`v2/agent.py`; `build_graph(steps)` builds a linear graph)
```
START → confirm_headers → prepare → filter_rows → choose_line_tables → map_line_columns → find_anchors → attach
      → resolve_anchors → snapshot → report_missing → map_statuses → END
```
State: `WorkflowState(ShopState)` (`v2/state.py`), one field per step's output. Partial graphs rerun one step on a
saved state: `filter_graph` (up to step 0), `sales_lines_graph` (up to step 1), `column_mapping_graph`,
`find_anchors_graph`, `attach_graph` (no LLM), `anchors_graph` (attach → resolve_anchors), `snapshot_graph`,
`missing_columns_graph` (no LLM), `statuses_graph`, `filter_statuses_graph` (filter_rows → map_statuses, for a state
saved before the filter step), `fill_missing_graph` (step 3's first gate-less trial, kept to compare).

**Next:** step 6 (forward fill first: step 5's blank rule depends on it); the status gap of dev_012 / dev_014.

### Shared fixed steps (`explore_agent/nodes.py`)
- `confirm_headers`: one structured call per group of xlsx tables the README does not describe (first 10 rows +
  "our guess is row N"). Header trust: confirmed > README (the row holding ≥ half the README's column names) >
  guess. Output `state["header_rows"]` (`explore.table_key(file, sheet)` → row).
- `prepare` (no LLM): README + `explore.list_files` into the state.
- `explore.py` conventions: a table = xlsx sheet / csv / sqlite table addressed by `(file, sheet)`; rows numbered as
  in the file (row 1 = first row); a **table group** = tables with the same header row and column names (month
  sheets, split files); `column_values` / `find_rows` without a sheet also read the split files. Try a tool by
  hand: `scripts/checks/try_explore.py <split> <shop> <tool> key=value...`.

## Step 0: filter the junk rows (built: `v2/filter_rows/`, node `filter_rows`)
Marks **repeated header rows** (dev_002 repeats its header 420 times inside the data) and **subtotal / footer /
note rows** (`Cộng ngày 01/05/2025`, `TỔNG THÁNG`, `Ghi chú: số liệu ghi tay`) on every table group, before step 1.
Why first: step 5's status words would otherwise include the header text, and they add noise to every later check.
Replaces the hand-written `common.SUBTOTAL` (kept as the answer key). Forward fill and duplicate lines need the
order-id column, so they are step 6.

**The rule (agreed):**
1. **Repeated header rows: code.** ≥ 2 cells equal their own column name. Always marked.
2. **Candidates: code, by shape, no words.** *Dense columns* = filled on ≥ 95 % of the group's data rows; a row
   blank in any dense column is a candidate (a subtotal is a label + a total, blank where every line is filled).
   Forward-filled columns are blank on many rows, so they are never dense and do not make rows candidates.
3. **Patterns: code.** Candidates are grouped by their word cells (a run of ≥ 3 letters), digits → `#`:
   `Cộng ngày 01/05/2025` … → `Cộng ngày #/#/#`. Money / number cells and the filled-column shape stay out of the key
   (with them one label split into many patterns). **A candidate with no word cell is keyed by its filled columns**
   (agreed: Claude's proposal): `(no text: Thành tiền)` vs `(no text: Ngày, SL, Thành tiền)`; else label-less totals
   and number-only lines would share one key and one verdict.
4. **The LLM judges each pattern** (`prompts.FILTER_ROWS`, one call per group with candidates, agreed): README
   meanings, table name + row count, 3 normal rows, then per pattern ("Group N") its row count and up to 3 example
   rows spread out (first, middle, last), each with its row above and below, marked `>`; blanks shown as blanks.
   Why the neighbours: a label-less total is visibly the sum of the lines above. Reply `group_1 … group_n`, enum
   `"data row"` / `"summary or note row"` (agreed as `"sales data"`, changed while building: every table is
   filtered, product and customer lists too). Every row of a pattern gets its verdict.

**Output and readers:**
- **Mark, don't delete** (agreed): `state["skipped_rows"]` = `{table_key: [row numbers]}`, carried like
  `header_rows`. `explore.Table` carries `skipped` and offers `data_numbers()` / `data_rows()`; every reader of data
  in steps 1–5 uses them (sample rows, row counts, checks, grids, profiles, attach, `column_values`). **`peek` and
  `find_rows` show the file as it is** (user). A state saved before the filter step has no `skipped_rows`: partial
  runners then read every row.
- **`subtotal_labels`** (user's idea: a per-shop regex instead of the global `SUBTOTAL`): `state["subtotal_labels"]`
  = `{table group: [regex]}`, one per "summary" pattern with text. *Built as Claude proposed, details not
  discussed:* code builds it from the key (escape, `#` → `\d+`, anchored, case-insensitive), matched against the
  row's word cells joined by ` | ` (a note for write_code), and checks it matches no row kept as data. No-text
  patterns get no regex (the converter uses the row numbers).
- **No handling of a failed / unreadable call yet** (user: add it when a shop shows one): with no reply no
  candidate is marked; repeated headers still are.
- **Weak spots (agreed: no fix until a shop shows one):** too many candidates when a column is blank on 0–5 % of
  rows, and names in word cells give one pattern per row (dev_014's instalment sheet: 17 one-row patterns); possible
  fix: a cap on patterns per call. Missed candidates if subtotals are frequent (no dense columns); possible fix: rows
  with fewer than half the usual filled cells. *Later idea, not built:* code checks whether a label-less number
  equals the sum of the lines since the previous candidate.

## Step 1: choose the line tables (built: `v2/find_line_tables/`, node `choose_line_tables`)
- Options = the table groups with data rows, so month sheets and split files are one option and `line_tables`
  follows from the choice (one `TableRef` per sheet / file). Names (`nodes.option_names`): `X.xlsx [S]`,
  `X.xlsx (17 sheets)`, `X_1.xlsx + X_2026.xlsx (19 sheets)`, `pos.sqlite [chi_tiet_don]`; steps 0, 3 and 5 use the
  same names.
- One option → no LLM call. Otherwise one call (`prompts.LINE_TABLES`: README, per option its tables, row count,
  columns, 3 data rows): a sales line names a product and a quantity; choose the line table over an orders table,
  the orders table only when there are no product lines (biz_110, dev_009), never lookup or event tables. Reply:
  one enum field `option`.
- **Left out on purpose (agreed):** retry (an unparsable reply leaves `line_tables = []`), adding near-identical
  groups (agreed fix when needed: code adds groups that differ by 1–2 columns), a plausibility check.

## Step 2: map the line columns (built: `v2/column_mapping/`, node `map_line_columns`)
A small agent: subgraph `START → answer ⇄ check → llm_check → END`, run by the parent's node (the subgraph's own
state stays inside; adding the compiled subgraph as a parent node would append the trace twice).
- **Asked from the schema side** (user): `ASKED` = `MAPPABLE` + `OPTIONAL` without `line_product_name`. The reply
  has one field per asked column, each an enum of the line table's column names + `"(not in this file)"`
  (`NOT_HERE`, leaves the field open for step 3). No invented, accent-stripped, forgotten or doubled names by
  construction. One table column may answer several fields (`3 chai` → qty + unit; date-time → date + time).
- **Not asked** (user): `line_no` (no query tool or rule uses it, and an `STT` running number was taken for it
  instead of the order id); `return_date` (`DERIVED`: same column as `order_date`, the converter splits it via
  `return_lines`).
- **Prompt** (`prompts.LINE_COLUMNS`): README meanings of the table's columns, the table as a grid (header + 5 data
  rows), each asked field with its meaning (`COLUMN_MEANINGS`, shared with step 3).
- **Checks** (code, every data row): **column checks** (`checks.check_answer`): ≥ 80 % of the column's non-blank
  cells read as the field's kind (dates, times, money, quantities; blanks not counted, so forward-fill gaps do not
  fail a column); `status` ≤ 40 distinct values. **Cross-column check**: `line_amount = qty × unit_price −
  line_discount` on ≥ 90 % of the lines, and no unit with ≥ 10 lines missing on more than half of them.
  **Shared-column check** (user): no two fields on one column except `qty` + `unit`, `order_date` + `order_time`
  (dev_003 put `qty` and `qty_base` on one column; they differ only on a few `hộp` lines, too few for the equation).
- **The amount fields are code's** (user, `amounts.py`): an answer that does not fit the equation is repaired by
  code (fewest changes among the columns that read as the field's kind; "no discount column" wins a tie); a
  remaining tie goes back to the model. `qty_base` is decided by code (kept when `qty_base / qty` behaves like a
  conversion factor, else another passing quantity column, else open). Why: the model's qty / qty_base answers
  depended on the prompt's layout. Known: biz_124 gets no discount column though `Khuyến mãi` fits too; llm_check
  reviews the open `line_discount`.
- **Retry inside the subgraph** (user), at most 3 replies: problems → a **fresh self-contained call** (user)
  asking only the failed fields: README meanings, the table plus up to 3 **evidence rows** per shared column (rows
  where it and its look-alike columns differ), the problems **in two parts** (user: the failed answers one per line,
  then the explanation), the fields. Without evidence rows and the two-part layout the 27B swapped or kept the wrong
  answers. An unreadable reply → the same request + that reply + `REPLY_PROBLEM`. After the last reply: a field that
  failed its column check is left unmapped; cross-column / shared-column fields stay mapped; all in
  `column_problems`.
  - Rejected: listing every earlier attempt's problems in the retry (the model repeated a failed answer anyway).
  - dev_009 left as it is (user): `'2025-01-01 00:00:00'` cells read as times, so the model keeps `order_time ←
    Ngày tháng`; the final mapping is still right (check + llm_check remove it). Possible fix, not built: a
    `00:00:00` time does not count as a time.
- **llm_check** (user's idea): one fresh call reviewing the final mapping of the fields code cannot check the
  meaning of. Locked (shown, not asked): the mapped amount fields when the equation passed, and `qty_base`. Each
  mapped field shows its column's profile over all rows (filled, distinct, 5 most common: a 5-row grid hides a
  constant shop-id column offered as seller). Reply: the answer's shape (one enum per reviewed field); each changed
  field is a fix, trusted (user) unless it fails its column check or breaks the equation. A failed call changes
  nothing. Rejected: a list of `{field, reason, column}` fixes (the 27B padded it until max_tokens). Fallback if
  its fixes turn out bad (user): split the `answer` job into smaller calls.
- **Rejected (user):** a constant-column check ("too rigid for the future unseen data"); the user's fix is
  temperature 0 + llm_check.

## Step 3: fill the open columns from other tables (built in part: `v2/fill_missing_schema/`)
```
find_anchors → attach → resolve_anchors → snapshot → resolve (fields; not built)
```
### Anchors (agreed)
- **An anchor** is a column holding the same values in two tables: order code, product code or name, customer code
  or name (`anchors.ANCHOR_FIELDS`). Another table feeds the lines only through an anchor (a left merge = a plan
  `Join`), so its anchor column must be unique, and anchors chain (customers → orders → lines in biz_108).
- **Why a gate:** the user's first trial (`fill_from_tables`: every other table asked every open field) took event
  tables' dates as `order_date` and a customers table's own status as the order's. So only tables that attach are
  asked, and each only the fields of the anchors it attaches by (`ANCHOR_MAP`).
- **By meaning, proven by values:** the LLM says which column is an anchor (names differ across files: `Ma don` ↔
  `Mã chứng từ`); code checks every claim by its values (matching values alone is noisy).
- **Anchors first** (user): find every table's anchors before any attach check, so attaching needs no LLM rounds.
- `fields_to_fill` (built by the user, `nodes.py`): the asked columns step 2 left open or failed, kept if another
  table can hold them (`FILLABLE`); `LINE_ONLY` ones go to step 4. Open points recorded with the user's answers:
  `line_product_name` is never reached (decide later); `unit` / `qty_base` as `LINE_ONLY` (discuss later).

`ANCHOR_MAP` (agreed: "go with that first, if anything weird happens we can fix"):
```
order_id      → order_date, order_time, status, channel, payment_method, seller, customer_name, customer_code, customer_group
customer_code → customer_name, customer_group
customer_name → customer_group, customer_code
product_code  → product_name, product_group, base_unit
product_name  → product_code, product_group, base_unit
```

### find_anchors (agreed; `find_anchors/`)
One call per non-line table group (`prompts.TABLE_ANCHORS`: README meanings, grid, the 5 anchors with meanings; a
column counts when the table only refers to the anchor, which finds the chain). Reply: one enum per anchor. The line
tables' anchors are step 2's mapping. Claims are kept as given; `attach` checks them. No retry. Output
`state["anchor_tables"]`: `{"table", "line", "anchors": {anchor: column}}` per group.

### attach (agreed; `attach.py`, code only)
- In rounds from the lines: table T attaches to an attached table A by anchor f when both claim a column for f, T's
  column is unique (`MIN_UNIQUE` 0.99 of filled cells distinct) and covers ≥ 50 % of A's distinct values
  (`MIN_COVERAGE`). Values compared without accents, case or extra spaces (`value_key`).
- It is also find_anchors' value check: a claim counts only if it reaches the lines (wrong claims confirm each
  other: biz_123's import tables share their receipt codes but never reach the lines).
- **Code + name together** (agreed; dev_011 / dev_014: the line's `Hàng` holds the `Ghi tắt` abbreviation on most
  lines, the full name on the rest): when T claims both columns of a pair (`PAIRS`) and attaches by neither alone,
  the two are tried as one column (each unique, both together unique, together ≥ 50 % coverage). Two links, each
  with `pair_with`. *Not discussed yet:* for the later fill, `product_name` should be the matched row's full name,
  the abbreviation must not become `product_code`, and the phone shops' `base_unit` is in no column.
- By design conversion lists and partial debt / returns lists do not attach.
- Output `state["attached"]`: the lines (round 0), then `{"table", "round", "links": [{"anchor", "column",
  "to_table", "to_column", "coverage", "unique", "pair_with"?}]}` = the plan's joins. Inputs also
  `state["dropped_claims"]`, `state["min_unique"]` (test knob).

### resolve_anchors (user's idea, agreed; `anchor_resolve/`)
Values cannot reject a true anchor in the wrong kind of table (a payments book's order ids). For each anchor that
several attached tables link by: one call (`prompts.CHOOSE_SOURCE`): which option is the shop's own list of orders /
products / customers, not one that records something else (payment, stock count, delivery). Reply: one enum of the
candidate names. **Pick one, no merge (agreed for now)**: the others' claims are dropped and `attach` reruns. A
failed call drops nothing. Test knob: `--min-unique 0.98` attaches the payments tables (just under 0.99 in train).

### snapshot (agreed; `snapshot/`)
One call per attached table (enum of its columns + `NOT_HERE`, step 2's column checks after, no retry), asking only
`fields_for(the anchors it attached by)`.
- **Never asked its own link anchors** (agreed): they only repeat the linked table's values and gave fake conflicts.
  A code + name pair counts as linked by both.
- **Every table asked on its own** (user, option a): conflicts show side by side for the field resolve. Rejected
  (b): round order dropping filled fields (the first table wins even when wrong).
- **`customer_group` meaning sharpened** (agreed, option 1): "the group the shop puts the customer in (lẻ / thân
  thiết / sỉ / VIP ...); not the customer type (cá nhân / công ty)": asked alone with no right column, the model took
  the nearest one. Option 2, not built: an llm_check-like review with column profiles.
- Output `state["snapshots"]`: `{"table", "round", "anchors", "asked", "filled", "failed"}` per table. *Not
  discussed yet:* telling the model which column the table attached by.

### Field resolve (agreed in shape: "1 agent to recheck"; details not discussed)
The user's design: for a field filled by several tables, the field, its meaning and each candidate column with 5
values → the LLM removes the wrong ones and says whether several are right (merging: not discussed). Proposed, not
discussed: postpone it (no field has had two passing candidates yet; `by_field` in the trace shows every candidate);
code could first check whether the candidates agree in values.

### Open points of step 3 (not discussed yet)
- `seller` as an anchor (biz_123's staff table; dev_006 has staff codes on orders and names in `nhan_vien`).
- Merging several right tables for one anchor (product list + stock report + drug list).
- Loosening `MIN_UNIQUE` (e.g. 0.95) now that the LLM removes payments tables.
- A tighter `order_id` meaning in find_anchors ("the code of a sale").
- No retries in find_anchors / resolve_anchors calls; add if needed.
- Step 2 with no line tables leaves every fillable field "failed"; step 3 then has nothing to attach to (user: the
  data is then not in a line file; counterpoint left open).

## Step 4: report missing columns (built: `v2/missing_columns/`, node `report_missing`, code only)
- **Found** (agreed) = step 2 mapped it, or an attached table filled it in snapshot. **Only `MAPPABLE` fields can be
  missing** (agreed). Output `state["missing_columns"]` = `{field: reason}` (user: the reason stays with the field;
  the plan's `missing_columns` = its keys). Reason codes (agreed): `line_only`, `failed_check`, `unreached`,
  `not_in_tables`, `failed_in_tables`.
- `ALWAYS_RECORDED` missing → a problem in the trace (agreed: an earlier step's mistake). `KNOWN_GAPS` marked:
  `unit` without a unit column (biz_128, dev_007, phone shops), `base_unit` (waits for conversions), only on shops
  with product lines.

## Step 5: status words (built: `v2/status_words/`, node `map_statuses`; every part agreed with the user)
Output: `statuses` (one `StatusWord` per raw word) and `blank_status`, both written by `map_statuses`.
- **5a, the column** (code, `words.status_column`): step 2's `status`, else the first snapshot table that filled
  it. **Open (known gap, not discussed yet):** dev_012 and dev_014 have no status column (status in a notes column /
  repair tickets steps 2–3 do not reach); step 5 skips them with the reason.
- **5b, the words** (code, `words.status_words`): distinct stripped non-blank values over the group's data rows,
  with counts and each word's first row.
- **Hybrid** (user; replaced "map_status answer key only"): a word in the known-word list (`parse.STATUS_MAP`,
  matched by `norm_text`) takes its status from it, no LLM. Why: some words are dataset conventions checked against
  the answer key that no model can work out (`Đang giao dịch` → in_delivery, `Không giao được` → cancelled; even
  Sonnet needed the train answers), unseen shops come from the same dataset, and the LLM still covers new words.
  Test knob `state["known_status_words"] = False` (runner flag `--no-known-words`): every word goes to the LLM, to
  measure it on train / public test, where the list knows every word.
- **5c, the call** (`prompts.STATUS_WORDS`, one per shop when there are unknown words or leftover blanks): the
  rulebook's **§8 quoted** (`utils.rulebook_sections`; the final rulebook may differ; §1 dropped by the user as
  revenue noise), one line per status saying what it means, never which words (`prompts.MEANINGS`), `Column '<name>'`
  with its README meaning (no table name: user), this shop's known words as examples, the words to map with
  counts, one row per word. Reply `word_1 … word_n` (+ `blank`), each an enum of the four statuses: every word gets
  one status, so `plan_check`'s status checks pass. No retry. All or nothing: a failed call leaves `statuses = []`.
  - Rejected (user): rewording the in_delivery line ("... still being processed, delivered or waited for"), whose
    "delivered" may have led the LLM to map `Đã giao` to in_delivery. Why: the known list covers such words, and a
    new word comes with the shop's known words as examples.
- **Blank statuses** (user, all three points agreed). A blank means: a forward-fill continuation line, a subtotal row
  (now removed by step 0), or, when the status sits in a notes column, completed (rulebook §8).
  1. Code finds the **leftover blanks**: a blank status on a row whose order id is filled (no order id: any blank).
     A blank on a continuation line (blank order id) repeats the order's first line (step 6's forward fill). In the
     known shops every blank of a real status column is a continuation line; dev_011 / dev_013 (notes columns) have
     leftover blanks. Order id = step 2's `order_id`, or the snapshot table's attach column.
  2. **The LLM decides their meaning**, in the same call: one more field `blank` with the count and two example
     rows. Why not code saying completed: the meaning comes from the rulebook, which may differ.
  3. **`SalesLinePlan.blank_status: Status | None`** (state `blank_status`; `statuses` cannot hold it: a `StatusWord`
     with `raw = ""` fails plan_check). The converter fills forward first, then leftover blanks take `blank_status`.

## Step 6: cleaning inputs (not decided; next to discuss, forward fill first)
*(proposed)* code: forward fill from blank order ids (the order-level columns blank on the same rows;
`forward_fill_columns`, order id first); `return_lines` from negative quantities. Step 5's blank rule uses the same
signal (blank order id = continuation line). Duplicate lines within an order also need the order id, after forward
fill. Service-fee / non-revenue rows: code finds candidates by known words (Phí, Voucher, Ship, Điểm), the LLM says
yes / no per candidate (*proposed before the no-hard-coded-words rule*; weigh against it and against the status
hybrid, where a known list was accepted for meanings).

## Step 7: leftovers (not decided)
*(proposed)* `unmapped`: code lists every column no step used. `extra_attributes`: postpone (no schema columns yet;
e.g. drug lists' active ingredient). Odd structures (dev_002 toppings inside a drink's cell, dev_012 one ticket per
row, plate inside `Xe`): the likely candidate for agent mode or a free-text note.

## Conversions: postponed (user)
Finding the conversion source (`conversions` / `conversion_table`: a table, the README list, or none; `column` is
step 2's `qty_base`) is its own step: a conversion table repeats a product per big unit and lists only some products
(fails attach), and gives a factor keyed by (product, unit). Formats differ (3-column tables in biz_117 / 122,
dev_002; nested factors in dev_005; catalog text in dev_013; README lists parsed by `common.readme_conversions`).
Postponed because no later explore step reads it; it matters to the converter (`unit_factor`, `qty_base`,
`base_unit`, `unit` where the line has none). Risk: a missed source gives `unit_factor = 1`, which `schema.validate`
cannot see. Proposed, not discussed: a `run_and_check` check for lines whose `unit` differs from the product's
`base_unit` while `unit_factor = 1`.

## Facts the steps depend on
- **README column meanings** (`readme.readme_columns(shop_dir, file, sheet)` → `columns`, `meanings`): parsed from the
  `## Cột trong \`File\`` + `- **Col**: meaning` lists of 37 of the 44 shops; the strongest clue for any mapping,
  shown in every prompt. The 7 shops without lists have only column names and sample values.
- **Joins on names** (biz_116, biz_122 product list, dev_011 `Ghi tắt`, dev_013 DanhMuc): the converter matches names
  accent-insensitively (`common.match_names`).
- **sqlite shops** (biz_123, dev_006): "file" = the .sqlite file, "sheet" = the table name.
- A shop can have both a `qty_base` column and a README conversion list (biz_108: the list kept for `unit_factor`).

## Runners (`scripts/explore/`, shared code `v2_runner.py`)
Flags: `--model`, `--max-tokens`, `--temperature` (default 0), `--workers`, `--out-dir`. Those that call the model
load `.env`, so the user runs them. Output: `runs/explore/<timestamp>-<suffix>/<shop>.json` (summary, usage, the
whole state incl. the trace, rewritten after every step) + `_summary.txt`; `show_run_v2.py <folder or file>`
rebuilds the summaries. Put the run folder **before** `--shops` (it swallows a folder after it).
- `run_explore_v2.py [--split] (shop ... | --all) [--no-known-words]`: the whole explore_agent → `-v2-full/`.
- `run_filter_v2.py [--split] (shop ... | --all)`: up to step 0 → `-v2-filter/`.
- `run_sales_lines_v2.py [--split] shop ...`: up to step 1 → `-v2-lines/`.
- On saved states (`<run folder> [--shops ...]`): `run_column_mapping_v2.py` (step 2), `run_find_anchors_v2.py`,
  `run_attach_v2.py` (no LLM), `run_resolve_anchors_v2.py [--min-unique 0.98]`, `run_snapshot_v2.py`,
  `run_missing_columns_v2.py` (no LLM), `run_statuses_v2.py [--no-known-words]` (summary: each word known / LLM
  marked ✓ / ✗ / ? against `parse.map_status`, leftover blanks → `blank_status`), `run_fill_missing_v2.py`.
- Checks (`scripts/checks/`, no LLM): `check_plan_check.py`, `check_v2_line_tables.py` (step 1), `check_anchors.py`
  (step 3 by values), `check_filter.py` (step 0 vs the `SUBTOTAL` answer key + a fake model), `check_readme_headers.py`.

## Code layout (under `src/datamate/analyst_agent/explore_agent/`)
LangGraph layout: a folder that builds a graph has `agent.py`, with `nodes.py`, `state.py`, `prompts.py` beside it.
```
explore_agent/
├── explore.py        tools as plain functions (list_files, table_groups, tables, peek, column_values, find_rows,
│                     column_index); Table.skipped + data_numbers() / data_rows()
├── readme.py         README column lists (readme_columns)
├── plan_check.py     MAPPABLE, OPTIONAL, DERIVED, ALWAYS_RECORDED, check_plan
├── state.py          SalesLinePlan (+ TableRef, ColumnSource, Join, StatusWord, Status, ExtraAttribute),
│                     ExploreContext (model, rulebook), ShopState
├── nodes.py          confirm_headers, prepare
├── utils.py          rulebook_sections, usage, since, label
├── v1/               the old agent loop (think ⇄ search subgraph → write_plan), kept as the base of agent mode
└── v2/               the workflow
    ├── agent.py          NODES, build_graph, explore_agent + the partial graphs
    ├── state.py          WorkflowState: skipped_rows, subtotal_labels (0); line_tables (1); columns, open_columns,
    │                     column_problems (2); anchor_tables, attached, dropped_claims, min_unique, fields_to_fill,
    │                     snapshots (3); missing_columns (4); statuses, blank_status, known_status_words (5)
    ├── utils.py          call_structured, dump_state / load_state
    ├── filter_rows/      step 0: rows.py (find_rows, pattern_key, pattern_regex, row_text, DENSE), nodes.py
    │                     (filter_rows, filter_group, verdict_schema), prompts.py
    ├── find_line_tables/ step 1: nodes.py (choose_line_tables, option_names, line_table_candidates), prompts.py
    ├── column_mapping/   step 2: agent.py (subgraph + map_line_columns), nodes.py (answer, check, llm_check, ASKED,
    │                     line_columns_schema, line_tables_of, table_text, readme_meanings_text), checks.py,
    │                     amounts.py, state.py, prompts.py (COLUMN_MEANINGS, NOT_HERE, ...)
    ├── fill_missing_schema/ step 3: anchors.py (ANCHOR_MAP), nodes.py (fields_to_fill, named_groups,
    │                     table_snapshot, the trial fill_from_tables), find_anchors/, attach.py, anchor_resolve/,
    │                     snapshot/
    ├── missing_columns/  step 4: nodes.py (report_missing, KNOWN_GAPS)
    └── status_words/     step 5: words.py (status_column, status_words, leftover blanks), nodes.py (map_statuses,
                          KNOWN, status_message, status_schema), prompts.py (STATUS_WORDS, MEANINGS, blocks)
```
Import: `from src.datamate.analyst_agent.explore_agent.v2 import explore_agent` (or a partial graph).

## Working rules (from the user)
- Every step must work on a ≤ 9B model: few choices, short outputs, code wherever it can do the work.
- The user is learning LangGraph: one concept at a time, name the built-ins used, explain changes.
- Discuss the design of each undecided part with the user before building it. The user takes proposals one at a
  time: record each idea as **agreed**, **not discussed yet** or **rejected**, exactly as the user said.
- The user runs the live runs (and check scripts when needed); give the commands, wait for the report. For a
  code-only change no check script is needed: the user judges from the run results.
- Never read `.env`; ask before running anything that loads it.
- Run results go in the reply, not in this file or `problem.md`.

## Environment
- Windows. Python: `.venv/Scripts/python.exe -I` (pandas, openpyxl, pyarrow, langgraph, langchain; uv project, the
  venv has no pip: ask the user to install a missing package).
- `-I` drops the project root from `sys.path`: scripts insert it before `from src.datamate ...`. The console is
  cp1252: a script printing Vietnamese calls `sys.stdout.reconfigure(encoding="utf-8")`.
- Bash output: redirect to `./cmd_output.txt` (project root) and read it back; `/tmp` does not work.
- Structured output: `with_structured_output(..., method="json_schema")`.
- Generated data: `data_std/<split>/<shop>/` (references, never hand-edited); agent output `data_std_agent/`.
