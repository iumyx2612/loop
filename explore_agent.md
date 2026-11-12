# explore_agent: a fixed workflow that writes a shop's SalesLinePlan

Handoff for whoever works on the explore_agent. **Self-contained**: read it alone to work on the explore_agent.
`problem.md` (project root) is the whole project's history; open it only for something outside the explore_agent.
This file holds decisions, designs and their reasons; run results go in the reply, never here. Exception (user,
2026-11-11): a model's weak spots found in a run, written as findings with their causes and options (see "Weak spots
of a 9B model").

Order: project context → workflow-wide decisions → the graph and each step (0–8) → facts → runners → code layout →
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
- **Competition rule: all models used at inference (LLMs, embedding models, rerankers, classifiers) total at most
  12B parameters** (public weights released before 26 Oct 2026, fine-tuning and quantization allowed, declared in
  the README). The LLM target is ~9B (not chosen yet). Development is on Qwen3.8-27B (the user's server); every step
  must stay simple enough for ~9B.

### The two agents
- **query_agent** (`src/datamate/query_agent/`, built): answers one question with tools over a shop's converted
  `sales_lines` table (`data_std/<split>/<shop>/sales_lines.parquet` + `manifest.json`). Never reads raw files.
- **analyst_agent** (`src/datamate/analyst_agent/`, in progress): turns an unseen shop folder into
  `sales_lines.parquet` + `manifest.json`, once per shop, offline. Scope: **sales_lines only** for now. Planned:
  `explore_agent → write_code → run_and_check (→ back to write_code on failure) → manifest + brief`.
  - **explore_agent** (this file): reads the README + files and produces a `SalesLinePlan`. Built: steps 0–5 (step
    3 without its field resolve); step 6 (6a–6c, code); step 7 (line kinds, LLM); the conversions step
    (`map_conversions`, LLM + code); step 8 (plan put together + `plan_check`, code). Every plan field is filled
    except `extra_attributes` (postponed).
  - **write_code** (started 2026-11-12, see "write_code"): a fixed code pipeline applies the plan (no LLM in any of
    its stages); only what it cannot convert goes to the LLM, as predefined functions whose parameters the LLM fills.
    The explore_agent outputs only the plan; applying it is write_code's job.
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
  (a list of `ConversionSource`, see "Conversions"), `return_lines`, `service_fee_names`,
  `non_revenue_names`, `subtotal_labels`, `extra_attributes`, `unmapped`, `notes`.
- **`Join`**: rows of `file` attach to rows of `on_file` where `key` (or `alt_key`, a code + name pair) = `on_key`; `on_file` may be a table joined
  earlier (biz_108: customers → **orders** by `Mã KH` = `Mã khách hàng`, orders → lines by `Mã hóa đơn`).
- **Column classes** (`plan_check`): `MAPPABLE` (must be mapped or listed missing; `line_no` is not in it: see step
  2), `OPTIONAL` (qty_base, line_discount, customer_code, line_product_name: may be mapped; the last two are
  working columns the converter reads but does not store), `DERIVED` (customer_key, product_key, line_kind,
  is_return_line, return_date, unit_factor, source_*: never mapped), `ALWAYS_RECORDED` (order_date, line_amount).
- **`plan_check.check_plan`** (no LLM; each problem says what to do): every column mapped or missing, names exist,
  a used file is a line table or joined, `cost_price` from a line table; joins (`on_file` attached before, key
  unique, ≥ 50 % of `on_key` values found: `MIN_JOIN_COVERAGE`); statuses (every distinct raw value listed, none
  invented, ≤ 40); forward fill (starts with the order-id column); texts exist; return_lines ↔ negative qty;
  conversions (per source: only its kind's fields, columns exist, factor cells read in `factor_format`, README
  list / quoted text present). `scripts/checks/check_plan_check.py` (a hand-written biz_108 plan
  passes, broken copies fail). **v2 mode** (`check_plan(..., skipped=state["skipped_rows"])`, built 2026-11-11 for
  step 8): reads data rows only (the filter step's rows left out), `subtotal_labels` are regexes (each must compile
  and match a line row), service-fee / not-revenue names may also be in a joined table (code-only shops). Without
  `skipped` (the hand-written plans of `check_plan_check.py`) every row is read and `subtotal_labels` are text.
- **Values computed from several rows / columns are never part of the mapping**: the mapping only says where a raw
  value lives. Forward fill (step 6), return-line dates and the returned status (fixed code via `return_lines`),
  qty_base / implied discount (`DERIVED`), values in another file (step 3), odd structures like dev_002 toppings
  inside a drink's cell or dev_012 values inside the `Xe` cell (step 8).

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
  (`think ⇄ search → write_plan → plan_check`) produced plans that passed `plan_check` but were wrong: one reply
  filling all 16 plan fields translated correct notes wrongly. **v1 deleted** (user, 2026-11-11: not needed any more);
  agent mode, if a step ever needs it, is built fresh. Since then no LLM fills `SalesLinePlan`: it is plain data.
- **Agent mode only as a fallback**, for a step genuinely too flexible for a workflow (candidates: odd structures,
  step 8).
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
*Session 2026-11-10:* step 6 designed and built (6a forward fill, 6b return lines, 6c duplicate count, 6e
service-fee / non-revenue names, renumbered step 7 on 2026-11-11), the dev_012 / dev_014 status gap written up (step 5a), the competition's model rule
corrected (all models ≤ 12B in total).
*Session 2026-11-11:* 6c leaves step 7's not-revenue rows out (so `map_line_kinds` runs before `cleaning_inputs`);
6e renumbered step 7 and the leftovers step 8; step 8 built (`write_plan`, plan_check's v2 mode, `skipped_rows`,
the conversions placeholder, 8a `unmapped`); `Join.alt_key` for code + name pairs; v1 deleted; the first whole run on
a 9B model (qwen3.5-9b, public test): its weak spots are in "Weak spots of a 9B model". On that run `write_plan`
built exactly the plan its (partly wrong) inputs say on all 14 shops (checked field by field against the state and
by rebuilding); the wrong meanings come from earlier steps.
*Session 2026-11-11, evening (the 9B weak spots):* the lesson: **the 9B misses things in a long or mixed prompt and
answers too quickly; one question at a time with a one-sentence reason before the answer works.** Built on that
pattern: step 5's blank call, step 7's pass 2, step 2's `open_fields` and `shared_recheck` (not working yet, see
step 2). Prompt changes are tried on saved states first (`zalo_main.py`, `scripts/explore/try_line_kinds.py`), then
built word for word as tested: the 9B's answer can flip on a small rewording.
*Session 2026-11-12 (the conversions step):* survey of every shop's conversion source (`survey/conversions.md`,
from the READMEs, the tables and the reference converters); the plan's `conversions` became a list of
`ConversionSource` (README list / table / README text) with its `plan_check` checks; `map_conversions` designed part
by part with the user and built (README list by code; else the README's last section asked line by line; which
table; its columns, with `key` and `factor_format` decided by code; README text read into rows). Two 9B weak spots
found on its first run and fixed (dev_004's README line, the base unit of code-keyed tables). Lesson again: **one
line per question** found what one question over the whole section missed. A shop can need two sources at once
(dev_004: a file and a README line).
```
START → confirm_headers → prepare → filter_rows → choose_line_tables → map_line_columns → find_anchors → attach
      → resolve_anchors → snapshot → report_missing → map_statuses → map_line_kinds → cleaning_inputs
      → map_conversions → write_plan → END
```
State: `WorkflowState(ShopState)` (`v2/state.py`), one field per step's output. Partial graphs rerun one step on a
saved state: `filter_graph` (up to step 0), `sales_lines_graph` (up to step 1), `column_mapping_graph`,
`find_anchors_graph`, `attach_graph` (no LLM), `anchors_graph` (attach → resolve_anchors), `snapshot_graph`,
`missing_columns_graph` (no LLM), `statuses_graph`, `filter_statuses_graph` (filter_rows → map_statuses, for a state
saved before the filter step), `line_kinds_graph` (step 7), `cleaning_inputs_graph` (6a–6c, no LLM; on a state
without step 7, 6c counts every row), `conversions_graph` (map_conversions, any state after prepare),
`write_plan_graph` (step 8, no LLM).

**Next** (updated 2026-11-12; the conversions step is done; the work moved on to write_code, see its own "Next" in
"write_code"; the items below stay open in the background):
1. dev_014's shared `Khách` column: `shared_recheck` answered "no" for both fields twice (see step 2); options (a)
   / (b) / (c) written there, not discussed yet.
2. The other 9B weak spots ("Weak spots of a 9B model"): dev_011 / dev_014 `Ghi tắt` (product_code not claimed, the
   abbreviation list does not attach); dev_012 `product_name` open (`Làm gì`). (429s: fixed by the host routing.)
3. Conversions leftovers (see "Conversions"): the guard (a missed source as a plan_check problem when the line has
   no `qty_base` or `unit`; user: after the baseline); a value check against a price taken as the factor (not seen
   on the 9B).
4. Step 8's open points (several attached tables filling one field: the first wins until the field resolve of step
   3; one Join per table); `extra_attributes` and odd structures (dev_002 toppings, dev_012 `Xe`), postponed.

### Shared fixed steps (`explore_agent/nodes.py`)
- `confirm_headers`: one structured call per group of xlsx tables the README does not describe (first 10 rows +
  "our guess is row N"). Header trust: confirmed > README (the row holding ≥ half the README's column names) >
  guess. Output `state["header_rows"]` (`explore.table_key(file, sheet)` → row).
- `prepare` (no LLM): README + `explore.list_files` into the state.
- `explore.py` conventions: a table = xlsx sheet / csv / sqlite table addressed by `(file, sheet)`; rows numbered as
  in the file (row 1 = first row); a **table group** = tables with the same header row and column names (month
  sheets, split files); `column_values` / `find_rows` without a sheet also read the split files. Try a tool by
  hand: `scripts/checks/try_explore.py <split> <shop> <tool> key=value...`.

## Step 0: filter the junk rows (built: `v2/planner/filter_rows/`, node `filter_rows`)
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

## Step 1: choose the line tables (built: `v2/planner/find_line_tables/`, node `choose_line_tables`)
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

## Step 2: map the line columns (built: `v2/planner/column_mapping/`, node `map_line_columns`)
A small agent: subgraph `START → answer ⇄ check → llm_check → shared_recheck → open_fields → END`, run by the
parent's node (the subgraph's own
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
  depended on the prompt's layout. An open `line_discount` is reviewed by llm_check and asked by `open_fields`.
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
  field is a fix, trusted (user) unless it fails its column check, breaks the equation, or **points to a column
  another mapped field holds** (`nodes.shared_fixes`, SHARED_OK pairs allowed; agreed 2026-11-11 after biz_119 on
  the 9B: `base_unit` → the `qty_base` column; the fix is the one dropped, so the field stays open for step 3). A
  failed call changes nothing. Rejected: a list of `{field, reason, column}` fixes (the 27B padded it until max_tokens). Fallback if
  its fixes turn out bad (user): split the `answer` job into smaller calls.
- **Rejected (user):** a constant-column check ("too rigid for the future unseen data"); the user's fix is
  temperature 0 + llm_check.
- **`shared_recheck` (agreed 2026-11-11, user: built straight into the graph; NOT WORKING YET).** Subgraph
  `… → llm_check → shared_recheck → open_fields → END`. A table column still holding several fields after the
  retries and llm_check (`check_shared_columns`, SHARED_OK pairs allowed) gets one call per field on it
  (`prompts.SHARED_FIELD`: the field's meaning, the column's README meaning, its profile and `other values:`, then
  `reason` first and `holds` yes / no). The "no" fields are unmapped only when another field on that column answered
  "yes"; all yes, all no or a failed call: unchanged, the problem stays. Then `check_mapping`, kept when the problems
  do not grow. Trace: `shared_recheck.columns[<column>]` = per field reason / holds / usage, `unmapped`, `verdict`.
  - Why: dev_014 keeps `customer_name` + `customer_group` on `Khách` (customer names; `lẻ` / `Khách lẻ` match the
    group meaning's example) through 2 retries and llm_check, so customer-group questions would be answered from
    names instead of abstained. Rejected before (step 3's snapshot, option a): unmapping every field on the column
    (loses the right one).
  - `other values:` (agreed): 5 values spread over the column's other distinct values, with counts
    (`nodes.other_values`), only in this prompt. Why: `Khách`'s 5 most common values are walk-in aliases ('Khách lẻ',
    'kl', 'lẻ', 'khách lẻ'), and the 9B answered customer_name "no" from them.
  - **Open:** even with the names visible the 9B answers both fields "no" ("generic terms like 'Khách lẻ' … alongside
    specific names, indicating customer type"): judged alone, a messy real column fails an absolute bar for every
    field, so nothing changes. Options (Claude's, not discussed yet; the user asked for a break here):
    - (a) one comparative question per shared column instead of a yes / no per field: "'Khách' is matched to
      customer_name and customer_group; which one does it hold?", enum = those fields + "none of them", reason
      first; keep the chosen field, unmap the others, "none" → unchanged (Claude's lean: a relative choice, still one
      short question about one column).
    - (b) quote the rulebook's row on walk-in customers (khách lẻ), if it has one; narrower.
    - (c) leave the warning and move on.
- **Pass 2, `open_fields` (agreed 2026-11-11, user's idea "isolate like step 7"; built).** Subgraph now
  `… → shared_recheck → open_fields → END`. For each open field of `OPEN_FIELDS` = status, qty, unit_price, line_amount,
  line_discount: code collects candidates = the line table's columns no mapped field uses that pass the field's
  column check; for status also text only (`reads_as_numbers` false). A field with candidates gets its own call
  (`prompts.OPEN_FIELD`): the field's meaning, each candidate with its README meaning and profile over all rows,
  then `reason` (one sentence, first) and `column` (enum of the candidates + NOT_HERE). Kept when `check_mapping`
  keeps it (an amount field may be set back by the repair) and the problems do not grow; a kept column is no
  candidate of a later field; a failed call changes nothing. Trace: `open_fields.fields[<field>]` = candidates,
  prompt, reason, column, verdict, usage (summed into the step's usage).
  - Why: a column whose values are rare or late in the table never shows in the 5-row grid (dev_011's status in
    `Ghi chú`, 29 return notes in 6,043 rows; dev_006's `ChietKhau`, 0 on the first rows), and llm_check profiles
    only mapped columns. Rejected: profiling the unused columns inside llm_check's 17-field review (the 9B still
    missed status and took `Khách trả` as customer_name by its name). Asked alone with a reason, the 9B found them
    and answered "none" elsewhere.
  - Left out (agreed): name / code / group / unit fields (no check: any text column passes); cost_price and
    order_date / order_time (first test: the 9B took dev_014's order discount `Bớt` as cost_price, nothing in code
    checks its meaning); qty_base (code's). Status: no "at least 2 distinct values" rule (rejected by the user: a
    file with one status word, or a notes column with one kind of note, would lose its real column); the 9B rejected
    dev_006's constant `IDCuaHang` itself.
  - Not covered: a field the first answer or llm_check mapped wrongly (pass 2 only fills open fields).

## Step 3: fill the open columns from other tables (built in part: `v2/planner/fill_missing_schema/`)
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
  with `pair_with`. Needs find_anchors to claim **both** columns: the 9B claimed only `Tên hàng` on dev_011 /
  dev_014, so the table did not attach (see "Weak spots of a 9B model"). *Not discussed yet:* for the later fill, `product_name` should be the matched row's full name,
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
- **Shared-column check + one retry** (agreed 2026-11-11, option b, after biz_109 on the 9B: `customer_group` and
  `customer_code` both on `Mã KH`; rejected: (a) failing both fields, which loses the right one): step 2's
  `check_shared_columns` on the answers that passed their column check, with one retry asking only the fields on a
  shared column (`prompts.OTHER_TABLE_RETRY`: the evidence rows and two-part problems of step 2's retry,
  `column_mapping.nodes.evidence_table`). *Built as Claude chose, not discussed yet:* the table's own link columns
  count as taken (attach proved they hold the anchor; links are never asked again, the retry lists them as known);
  a column still shared after the retry keeps its fields filled, as step 2 does, with the problem in the trace
  (`shared_left`), for the field resolve. Check `scripts/checks/check_shared_columns.py <run folder>...` (no LLM)
  replays saved runs through both new checks.
- **After the retry, two guards** (agreed 2026-11-11; why: the retry re-asks only the shared fields, so it cannot
  see that its new answer lands on a column a field outside the retry holds; biz_109's retry moved `customer_group`
  from `Mã KH` onto `Khách hàng`, customer_name's column, whose `lẻ` cells match the group meaning's example):
  1. **The retry's "Columns already known (not asked)"** lists the link columns **plus the filled fields outside the
     retry related to a retried field** (`anchors.related_fields`: the same `ANCHOR_MAP` family, `{anchor} + its
     fields`, `order_id` left out since it looks up nearly every field; `nodes.known_columns`, trace `retry.known`).
     A field asked again is never listed. Why related only (user): the model mixes up fields whose meanings are close
     (name / code / group); rejected: listing every filled field. Alone it did not help on the 9B, hence 2.
  2. **A retried field moved onto a column a field outside the retry holds is failed** (`nodes.moved_onto_taken`,
     trace `retry.moved_onto_taken`; any first-answer field that passed and was not asked again, or a link column;
     SHARED_OK pairs allowed), then the shares are checked again without it. Why: the other field passed both rounds
     untouched, so the retried one is wrong. A share among retried fields only still stays filled (`shared_left`).
  - **Rejected (user):** dropping or rewording the example words in `customer_group`'s meaning: it is shared with
    step 2 (`COLUMN_MEANINGS`), so the change would reach every shop's prompts.
- *Not discussed yet:* `OTHER_TABLE` says "The same table column may be the answer for several sales_lines
  columns" without step 2's examples (`3 chai`, date-time), which may invite shared answers.
- Output `state["snapshots"]`: `{"table", "round", "anchors", "asked", "filled", "failed"}` per table. *Not
  discussed yet:* telling the model which column the table attached by.

### Field resolve (agreed in shape: "1 agent to recheck"; details not discussed)
The user's design: for a field filled by several tables, the field, its meaning and each candidate column with 5
values → the LLM removes the wrong ones and says whether several are right (merging: not discussed). Proposed, not
discussed: postpone it (`by_field` in the trace shows every candidate); code could first check whether the
candidates agree in values.

### Open points of step 3 (not discussed yet)
- `seller` as an anchor (biz_123's staff table; dev_006 has staff codes on orders and names in `nhan_vien`).
- Merging several right tables for one anchor (product list + stock report + drug list).
- Loosening `MIN_UNIQUE` (e.g. 0.95) now that the LLM removes payments tables.
- A tighter `order_id` meaning in find_anchors ("the code of a sale").
- No retries in find_anchors / resolve_anchors calls; add if needed.
- Step 2 with no line tables leaves every fillable field "failed"; step 3 then has nothing to attach to (user: the
  data is then not in a line file; counterpoint left open).

## Step 4: report missing columns (built: `v2/planner/missing_columns/`, node `report_missing`, code only)
- **Found** (agreed) = step 2 mapped it, or an attached table filled it in snapshot. **Only `MAPPABLE` fields can be
  missing** (agreed). Output `state["missing_columns"]` = `{field: reason}` (user: the reason stays with the field;
  the plan's `missing_columns` = its keys). Reason codes (agreed): `line_only`, `failed_check`, `unreached`,
  `not_in_tables`, `failed_in_tables`.
- `ALWAYS_RECORDED` missing → a problem in the trace (agreed: an earlier step's mistake). `KNOWN_GAPS` marked:
  `unit` without a unit column (biz_128, dev_007, phone shops), `base_unit` (waits for conversions), only on shops
  with product lines.

## Step 5: status words (built: `v2/planner/status_words/`, node `map_statuses`; every part agreed with the user)
Output: `statuses` (one `StatusWord` per raw word) and `blank_status`, both written by `map_statuses`.
- **5a, the column** (code, `words.status_column`): step 2's `status` (a notes column holding statuses is found by
  step 2's `open_fields`), else the first snapshot table that filled it. No status column → step 5 skips the shop
  with the reason: no statuses, no `blank_status`.
  - **Open (not discussed yet):** dev_012 (repair tickets, one per row, no ticket code): `Ghi chú` holds notes, no
    status (`bảo hành`, `khách chờ lấy liền`, `hẹn chiều lấy` ...); the reference sets every ticket completed
    (rulebook §8, "Sổ không có cột trạng thái": a note not naming a status = completed). The plan says nothing, so
    the converter must default to completed; option: no status column → `blank_status` from the same §8 question.
- **5b, the words** (code, `words.status_words`): distinct stripped non-blank values over the group's data rows,
  with counts and each word's first row.
- **Hybrid** (user; replaced "map_status answer key only"): a word in the known-word list (`parse.STATUS_MAP`,
  matched by `norm_text`) takes its status from it, no LLM. Why: some words are dataset conventions checked against
  the answer key that no model can work out (`Đang giao dịch` → in_delivery, `Không giao được` → cancelled; even
  Sonnet needed the train answers), unseen shops come from the same dataset, and the LLM still covers new words.
  Test knob `state["known_status_words"] = False` (runner flag `--no-known-words`): every word goes to the LLM, to
  measure it on train / public test, where the list knows every word.
- **5c, the words call** (`prompts.STATUS_WORDS`, when there are unknown words): the rulebook's **§8 quoted**
  (`utils.rulebook_sections`; the final rulebook may differ; §1 dropped by the user as revenue noise), one line per
  status saying what it means, never which words (`prompts.MEANINGS`), `Column '<name>'` with its README meaning (no
  table name: user), this shop's known words as examples, the words to map with counts, one row per word. Reply
  `word_1 … word_n`, each an enum of the four statuses: every word gets one status, so `plan_check`'s status checks
  pass. No reason, no retry. All or nothing: a failed call leaves `statuses = []`.
  - Rejected (user): rewording the in_delivery line ("... still being processed, delivered or waited for"), whose
    "delivered" may have led the LLM to map `Đã giao` to in_delivery. Why: the known list covers such words, and a
    new word comes with the shop's known words as examples.
- **Blank statuses** (user, all three points agreed). A blank means: a forward-fill continuation line, a subtotal row
  (now removed by step 0), or, when the status sits in a notes column, completed (rulebook §8).
  1. Code finds the **leftover blanks**: a blank status on a row whose order id is filled (no order id: any blank).
     A blank on a continuation line (blank order id) repeats the order's first line (step 6's forward fill). In the
     known shops every blank of a real status column is a continuation line; dev_011 / dev_013 (notes columns) have
     leftover blanks. Order id = step 2's `order_id`, or the snapshot table's attach column.
  2. **The LLM decides their meaning**, in its own call (below). Why not code saying completed: the meaning comes
     from the rulebook, which may differ.
  3. **`SalesLinePlan.blank_status: Status | None`** (state `blank_status`; `statuses` cannot hold it: a `StatusWord`
     with `raw = ""` fails plan_check). The converter fills forward first, then leftover blanks take `blank_status`.
  - **The blank call** (agreed 2026-11-11, user: "reason on the blank status only"; built): when there are leftover
    blanks, its own call after the words call: the same prompt with the blank block instead of words (the words known
    or just answered shown as known), the line "First write one short sentence why (reason), then the status
    (blank)." (`prompts.BLANK_WHY`), reply `reason` then `blank`. A failed call leaves `blank_status` None, the
    statuses stay. Trace: the words call at the top of the record, the blank call in `blank_call` (prompt, reason,
    blank, usage); `usage` sums both. Why: without the reason the 9B answered `in_delivery` for notes columns where
    the rulebook's §8 notes row says completed; with it, it cites that row.
  - **The blank block** (agreed 2026-11-11), facts code knows, never which rulebook row applies (user: a code rule
    like "no status column → the §8 notes row" hard-codes the rulebook's structure, which the final rulebook may
    change): "The column is also blank on N of its R rows (P%, M orders). Each of these rows has its order id
    written, so the blank is not a later line of an order repeating the order's first line: the order itself has no
    word in this column." + two leftover rows **from different orders** (`Words.leftover_orders`). Without an order id
    column: "blank on N of its R rows (P%)". `Q_BLANK` = "which status does an order have when this column names no
    status for it (the cell is blank)?".
    - Why each part: the old "rows that start an order (on the other lines of an order a blank repeats the order's
      first line …)" was false for a shop writing the order id on every line (dev_013) and described §8's row about
      later lines of a group ("không có nghĩa là … đơn hoàn thành"); the old question "… blank on an order's first
      line?" pointed at the same row; the share makes "almost every order in delivery" look implausible.
    - Rejected: a code guard flagging `blank_status ≠ completed` (hard-codes the rulebook's answer); an LLM extracting
      the rulebook's status row first, then mapping blank from it alone (user's idea, dropped by the user: picking
      the row needs the shop's facts, plus a long-input call). Finding against "give the model only the rulebook
      part": in step 7 a question with only the rulebook rows misled the 9B (see step 7).

## Step 6: cleaning inputs (built: code-only node `cleaning_inputs` in `v2/planner/cleaning_inputs/`)
**Hybrid** (user): what needs no LLM is pure code. Forward fill, return lines and duplicates are one code-only node
`cleaning_inputs` (order inside: 6b → 6a → 6c); service-fee / non-revenue rows (step 7) are their own node **before it**
(agreed 2026-11-11: 6c leaves step 7's not-revenue rows out; step 7 reads no step-6 output).
Plan fields filled: `forward_fill_columns` (6a), `return_lines` (6b), `notes` (6c), `service_fee_names` and
`non_revenue_names` (step 7).
- **Placed after `map_statuses`** (user): step 5 does not need filled data. Forward fill only copies a word already
  on the order's first line, so step 5's words are the same; step 5's leftover-blank rule (`words.py`: a blank on a
  line with a blank order id is a continuation line) assumes what step 6 does. With "always fill" below the two
  cannot disagree.

### 6a Forward fill (agreed)
- **Continuation line** = a data row (after step 0) whose order id is blank. Rulebook §8 (train `rulebook.md`
  §8, "Ô để trống ở các dòng sau của cùng một nhóm dòng"): a blank cell on the following lines of a group is the
  first line's value (customer, notes, status), never missing data, walk-in or completed. So a blank order id is
  never a stand-alone sale.
- **`forward_fill_columns` = the order id + every order-level column step 2 mapped on the line table**
  (order_date, order_time, status, customer_name, customer_code, channel, payment_method, seller). **Always fill, no
  threshold** (user): forward fill only fills blank cells of continuation lines from their own order's first line
  (`common.forward_fill`) and never overwrites a filled cell, so a column with no blanks costs nothing and a column
  sometimes rewritten on continuation lines is still right. Empty list when no order id is mapped or it is never
  blank. Fields from an attached orders table come through the join, not forward fill.
  - Rejected: a threshold (column blank on ≥ 90 % of continuation rows; first agreed, then dropped by the user).
    It protected nothing, and a status column left out would get `blank_status` on continuation lines step 5 never
    counted.
  - Rejected: a check for "suspicious" continuation lines (most order-level cells filled = looks like a new order):
    §8 says a blank-id line is a continuation line, so it would report what the dataset defines as normal.
  - *Built as Claude chose, not discussed yet:* a column is listed only when it is blank on **at least one**
    continuation line (`plan_check` rejects a never-blank forward-fill column: "nothing to fill; remove it"; filling
    it would do nothing anyway). `ORDER_LEVEL` also has `customer_group`. The line tables of the group are read as
    one sequence (a continuation line at the top of a month sheet continues the last order of the sheet before, as
    when the converter concatenates them); lines above the first order id are counted as `orphans` (a trace
    warning: nothing to fill from).
- **Built** (`cleaning_inputs/forward_fill.py`, node in `cleaning_inputs/nodes.py`): output
  `state["forward_fill_columns"]`; the trace record's `forward_fill` holds the continuation count, per column its
  fields and blank continuation lines, the columns left out, the conflicts (up to 3 example orders per column) and
  the warnings.
- **Check (trace warning, no fix):** after filling, an order with two different values in a filled column. The
  only case where the two reference styles differ (below) and the rulebook says nothing. **Return lines left out**
  (user, option b): a return line carries the return's own date (dev_004: all 95 two-date orders were its 95 return
  orders), which the converter turns into `return_date`. An order differing only on return lines is counted
  (`return_only`), not warned about, so a real conflict between sale lines stays visible.
- **How the references fill** (no reference checks stand-alone lines): `common.forward_fill` (biz_122, dev_004,
  dev_005) fills only blanks of continuation lines, order id included, keeps filled cells, leaves lines above the
  first order id; `phone_shop.per_order` (dev_011 after `STT.ffill()`; dev_014 repeats STT on every line) gives
  each order-level column the first non-blank value of any line of the order (status from `Ghi chú`, blank →
  completed).

### 6b Return lines (agreed; built: `cleaning_inputs/return_lines.py`)
- **Return line** = a data row with a negative qty (step 2's `qty`, read by `parse.parse_qty`: `-2 kg` is −2), as
  in every reference (`is_return_line = qty < 0`) and `plan_check` (return_lines ↔ a negative qty). `return_lines` =
  at least one. False with the reason when qty is not mapped (one row per order / ticket). Output
  `state["return_lines"]`; the rows go to 6a's check.
- **What the references do with them** (biz_117, dev_004; the only converted shops with negative lines, one return
  line per order, negative qty and negative amount always together): the return line is its own line with its own
  order id and the return's date; `return_date` = that date; `order_date` / `order_time` = the earliest of the
  order's sale lines; every line of an order with a return line is `returned` (§1; a note when the written status
  disagrees); an order with only return lines gets no `order_date` (note). All converter code: the plan only says
  yes / no. biz_105 has return lines too but no reference.
- *Built as Claude chose, not discussed yet:* the decision is by qty only (as `plan_check`); trace warnings for a
  negative qty with a positive amount, a negative amount whose qty is not negative (discount / voucher rows: step 7),
  and qty / amount cells that do not read as numbers.

### 6c Duplicate lines (agreed; built: `cleaning_inputs/duplicates.py`)
Nothing to plan: the converter drops lines identical within an order after forward fill (§8). Code counts them
into `notes` for information. 6b counts return lines before this (dev_004: 96 vs the reference's 95, biz_117: 116
vs 114: the extra ones are duplicate return lines).
- Same rule as `common.drop_duplicate_lines`: rows filled with 6a's `forward_fill_columns`, then a row identical in
  every cell to an earlier one (cells as `explore._cell`, like the reference's `astype(str)`). The order id is a
  cell, so only rows of one order match; the group's tables are compared together. No order id → nothing counted
  (§8: a table not written by order counts both).
- Output: a line in `state["notes"]` (the plan's notes; 6c is its only writer so far) when there are any; the
  trace record's `duplicates` holds the count, the orders and up to 3 examples.
- **Not-revenue rows left out** (agreed 2026-11-11): rows named in step 7's `non_revenue_names` (dev_013: Voucher /
  Phí ship / Dùng n điểm) are never counted nor remembered as an earlier line, as the reference drops them before
  de-duplicating. Why: they were counted as duplicates the converter never sees. So `map_line_kinds` runs before
  `cleaning_inputs`. Service-fee rows stay in (revenue, kept). *Built as Claude chose:* a row's name = its
  `product_name` cell, else (code-only shops) the products table's name for its code
  (`line_kinds.candidates.products_table_names`), else the code, as step 7 names them; compared by `value_key`. Trace:
  `duplicates.not_revenue_rows`.

## Step 7: service-fee / non-revenue rows (agreed; built: `v2/planner/line_kinds/`, node `map_line_kinds`)
*Renumbered (user, 2026-11-11): was 6e. The node runs **before** `cleaning_inputs` (step 6), whose 6c reads its
`non_revenue_names`; the numbers follow the order the steps were designed in, not the graph order.*
**What it fills:** two plan lists of product-name values, not a column. `service_fee_names`: rows kept as revenue
but not an item (`line_kind = "service_fee"`); `non_revenue_names`: rows that are not sales (dropped).
- **Why both** (user asked whether a fee differs from a product, since revenue is one sum): revenue does not need
  it, item questions do. Rulebook §2 "Mặt hàng": a `Phí phục vụ` line is not an item (never ranked or counted) but
  is revenue, also in a product group's share denominator; §1 "Doanh thu một đơn": order discounts / vouchers are
  not subtracted, shipping not added. The query_agent already filters `line_kind == "product"` for product measures,
  group-by product, gross profit and the product list (`query.py`, `entities.py`). Without it "3 mặt hàng bán chạy
  nhất" on 125–130 would rank `Phí phục vụ` first. So the question per name is the rulebook's: an item / revenue but
  not an item / not revenue.
- **The rows in the known shops:** `Phí phục vụ` (train 125–130, dev_007, dev_008; reference `line_kind =
  "service_fee"` in biz_128, dev_007, dev_008): qty 1, unit `lần` or none, a price that changes per bill. dev_013
  `Voucher`, `Dùng n điểm` (negative amount), `Phí ship` (positive): amount only, no qty / unit / price; the
  reference drops them. biz_127 / 129 write only the code on the line: `Phí phục vụ` is the products table's name.
- **Rejected (user):** hard shape rules for candidates ("amount without qty and price", "price varies with qty
  always 1"): read off two layouts only, too rigid for unseen shops. **Rejected (user):** an embedding model to
  score names against anchor phrases (allowed by the competition, but short Vietnamese names embed poorly; it also
  cannot tell `Phí phục vụ`, revenue, from `Phí ship`, not revenue: a rulebook rule, not word meaning).
- **Agreed (user): ranking + top K** (option a). Code scores every product and the K highest go to the LLM, so a new
  kind of fee only has to land in the top K, not match a rule. Rejected alternatives: every name in batches (long
  enum replies on ≤ 9B), hard rules.
- **Key and name (agreed):** the key per line = step 2's `product_code` when mapped, else `product_name` (shops have
  50–170 distinct keys; odd: dev_002 438, dev_012 3,188 free-text jobs); the name shown = the line's (most common
  spelling per code) or, for code-only shops (biz_111, 123, 127, 129, dev_006, dev_010), the products table's
  through attach's `product_code` link and the column snapshot filled `product_name` from.
- **Selection (agreed, after measuring the signals on 11 shops):** the union of two lists, no weights:
  - the top `TOP_K = 15` keys by order share (rows when no order id): `Phí phục vụ` ranks #1–#2 in five restaurants,
    #11 in biz_128 (0.09 vs the top dishes' 0.12); dev_013 `Voucher` #1 (0.68), `Phí ship` #2;
  - every key with an unusual-number sign (blank qty, blank unit price, negative amount; a sign counts only when
    step 2 mapped that column) on ≥ `UNUSUAL = 50 %` of its lines, at most 15: dev_013's six rows are at 100 %, every
    real product of the 11 shops ≤ 1 % (biz_117's return lines); catches the rare points rows (#83 by share).
  - Measured and dropped: price variation (the fee ~0.7, but beer / water ~2.3 and grocery / pharmacy medians
    1.4–2.1: the same item by can or case), once per order (dishes too). Missed by design: a rare normal-looking fee.
- **Built as Claude chose, not discussed yet:**
  - Names differing only in digits are one question, grouped **among the candidates only** (grouping all names
    first would merge products and change their shares) and by real names only (never bare codes `SP0056`); the
    plan gets every spelling. Side effect: two products can share a question (`Sữa Similac 5G số # 900g`), harmless
    (both items).
  - The prompt (`line_kinds/prompts.py`) quotes two rulebook table rows found by their label, `Doanh thu một đơn`
    and `Mặt hàng` (not whole sections: §1 / §2 are long); the three kinds in the rulebook's terms (item = mặt hàng,
    revenue = doanh thu), "not revenue" with examples (discount / voucher on the whole order, points, delivery fee);
    per candidate its line count and order share, then one grid of two lines each (first and last seen; code-only
    shops get a `name (products table)` column). Reply `name_1 … name_n`, enum `item` / `revenue, not an item` /
    `not revenue`. No retry; a failed call leaves both lists empty.
  - Output `state["service_fee_names"]`, `state["non_revenue_names"]`; trace: the candidates (why picked, share,
    unusual share), the prompt, the answers. Runner `run_line_kinds_v2.py`, partial graph `line_kinds_graph`.
- **Pass 2: each non-item asked again alone, with a reason (agreed 2026-11-11, user; built).** After the call above
  (pass 1, unchanged), every candidate pass 1 did not call an item gets its own call: the same prompt with only that
  name, a `reason_1` field (one sentence, before the kind; description "one short sentence: why, for '<name>'") then
  `name_1`, and the line "For each name, first write one short sentence why (reason_1, ...), then its kind (name_1,
  ...)" (`prompts.WHY_LINE`), word for word as tested (a rewording flipped `Phí ship` back to revenue). Its kind
  replaces pass 1's (also "item"); a failed call keeps pass 1's. Trace: `answers` (pass 1), `recheck` (per name:
  prompt, kind, reason, usage), `final`; the record's `usage` sums both passes. Cost: one small call per non-item.
  - Why (tests on the 9B, `scripts/explore/try_line_kinds.py`): pass 1 put dev_013's `Voucher` (−10.000đ on 68 % of
    orders) in "revenue, not an item" though the "not revenue" meaning names vouchers. Not the cause: the other
    names, the label wording, "charged on the bill", "on the whole order", the option order. Only one name per call
    **and** a reason first got it right (all names + reasons: wrong; one name, no reason: wrong). Items were right in
    every variant, so pass 1 keeps sorting them.
  - The reasoning is in the prompt's control (user): the model's own thinking stays off (`v2_runner.make_model`).
    Free text only in one fixed field per call; no length limit in the schema (a reason failing validation would
    lose a right answer); max_tokens is the guard.
  - Finding (against giving the model only the rulebook part): a yes / no question with only the rulebook rows ("is a
    line named 'Voucher' part of the order's revenue?") answered yes, reasoning "Doanh thu = tổng thành tiền các dòng
    hàng" and the voucher is a line. Our plain meanings ("a discount or voucher …") are what corrected it.
  - Not covered: a fee pass 1 wrongly calls an item never gets a second look (as before).
- Earlier, not taken: a known-word list (`Phí phục vụ`, `Voucher` ...), as step 5's hybrid; these are plain words
  an LLM reads, not dataset conventions like `Đang giao dịch`.

## Step 8: put the plan together (built: `v2/planner/write_plan/`, node `write_plan`, code only)
Agreed with the user (2026-11-11): 8a `unmapped` by code; `extra_attributes` postponed (`[]`: the schema has no
columns for them yet); odd structures (dev_002 toppings, dev_012 `Xe`) postponed (agent-mode candidates); then one
code node puts the `SalesLinePlan` together and runs `plan_check` as the guard (problems in the trace, no retry).
- **Fields:** `line_tables` (1); `columns` = step 2's + the snapshot's fills (3); `missing_columns` = step 4's keys;
  `joins` = attach's links; `statuses`, `blank_status` (5); `forward_fill_columns`, `return_lines`, `notes` (6);
  `service_fee_names`, `non_revenue_names` (7); `subtotal_labels` = step 0's regexes of the line table group;
  **`skipped_rows`** (new plan field: step 0's rows of the line and joined tables, so the converter also drops the
  label-less rows no regex covers); `unmapped` (8a). Output `state["plan"]` (a dict: `SalesLinePlan(**plan)`) and
  `state["plan_problems"]`.
- **Conversions placeholder** (agreed; since 2026-11-12 `state["conversions"]`, `[]` + the note when the state has
  none): first `column` when step 2 mapped `qty_base`, else `none`, plus a plan note saying conversions were not
  explored. Why agreed: nothing reads the plan before write_code exists. Effect once a
  converter reads it: `unit_factor = 1` on big-unit lines, so `qty_base` questions (bán chạy, bán được bao nhiêu
  `<đơn vị>`, mặt hàng ế, hiệu ứng tăng giá) and the per-base-unit price are wrong where big units are sold; revenue,
  order, customer, cancellation and gross-profit answers are not affected. So the conversions step comes before
  write_code.
- **8a `unmapped`** (agreed, `write_plan/unmapped.py`): for the line tables and every attached table, each column no
  step used (not mapped, not filled, not a join column on either side, not forward filled) →
  `<table> / <column>: <README meaning, else up to 3 distinct values>`; every table that never attached → one
  `<table>: not attached (<rows> rows; columns: ...)`. For judging a plan by hand; the converter does not read it.
- **Code + name pairs: `Join.alt_key`** (agreed 2026-11-11): a pair from attach (`PAIRS`) is one `Join` with
  `key` = the pair's first column and `alt_key` = the other; a row attaches when the line's column equals either
  (dev_011 / dev_014: `Hàng` = `Ghi tắt` or `Tên hàng`). `plan_check` reads both, accent-insensitively: each column
  unique, no value naming different rows through the two, coverage over both (dev_014: 35 % on `Ghi tắt` alone).
  Replaced a free-text plan note the converter and `plan_check` could not act on. No prompt changed: no LLM fills
  `Join` since v1 was deleted.
- *Built as Claude chose, not discussed yet:*
  - a field filled by several attached tables: the first by attach round wins, the others in the trace
    (`alternatives`), until the field resolve exists;
  - one `Join` per attached table, by its first link (other links in the trace, `extra_links`);
  - a table group as one `TableRef`: every sheet of an xlsx → `sheet = None`; a group spread over several files
    (outside the line tables) → its first table, with a trace warning.
- **What the guard does not see:** `plan_check` checks structure (names exist, joins cover, every status word has
  a status), not meaning. A wrong `blank_status`, a status word on the wrong status, a voucher listed as a service
  fee or one raw column on two fields all pass; step 8 copies them into the plan unchanged, as designed.
- `explore._read`'s cache raised from 16 to 64 files: dev_002's 18 files evicted each other (step 8 took 86 s → 13 s).

## Conversions (design started 2026-11-12; survey: `survey/conversions.md`)
- **The step looks for the source in every shop** (agreed, user 2026-11-12): the source = where the
  shop writes its conversion factors (README list, a conversion table, a catalog column, README prose). Every shop's
  `sales_lines` gets `unit_factor` / `qty_base`; on shops whose lines carry both `qty_base` and `unit` the
  converter can compute the factor from the line, so there the source is a cross-check.
- **Output: `SalesLinePlan.conversions: list[ConversionSource]`** (agreed 2026-11-12, user: "implement it to get
  the baseline"; built in `state.py`, replaces `conversions` column / file / readme / none + `conversion_table`).
  `[]` = no conversions. A line's `qty_base` column is not a source: it is in `columns`. Per source `kind`:
  - `readme_list`: the README list, parsed by `common.readme_conversions`; no other field.
  - `table`: `table`, `key` (code / name), `product_column`, `unit_column` (None for text), `factor_column`,
    `factor_format` (`number`; `nested` = dev_005 `15 vỉ`; `text` = dev_013 `1 thùng = 4 bịch; 1 lốc = 4 gói`),
    `base_unit_column` (when the table has one). A catalog with a text column is a table, no kind of its own.
  - `readme_text` (dev_004 "Hàng theo bó"): `text` quotes the README, **`rows` (product, unit, factor, base_unit)
    are read out of it by the explore step** (agreed, user; rejected: quoting only and leaving it to write_code).
  - Why one list: a shop may have several sources (dev_004: a table + prose).
- **plan_check** (built as Claude chose, not discussed yet): per source, only its kind's fields; table columns exist;
  ≥ 90 % of the non-blank factor cells read in `factor_format` (`MIN_FACTOR_CELLS`); `readme_list` needs a list in
  the README; `readme_text` needs `text` found in the README, ≥ 1 row, positive factors, each row's product in the
  text. **Not checked:** that a number column is the factor and not a price per big unit (biz_109 `Giá bán`).
  `check_plan_check.py` has a correct source per format (biz_122, biz_109, dev_005, dev_013, dev_004) and broken
  copies.
- `write_plan` takes `state["conversions"]`; a state without it gets `[]` + a note (a state saved before the step).
- *Not discussed yet:* a missed source as a `plan_check` problem when the line lacks `qty_base` or `unit` (the factor
  or unit has no other source), only a warning otherwise (the guard, after the baseline: user).
- **Finding the sources** (node `map_conversions` in `v2/planner/conversions/`, right before `write_plan`: later parts need
  step 2's mapping and step 3's tables; runner `run_conversions_v2.py`, partial graph `conversions_graph`). Parts, one
  at a time:
  1. **README list: code** (agreed 2026-11-12): `common.readme_conversions` finds ≥ 1 row → `readme_list`, no LLM
     (it parses all 22 list shops, nothing in the other 22).
     **Hybrid fallback** (user's idea, agreed): no row found → ask the LLM whether the README itself writes
     conversions with numbers (not only names the file holding them), reason first, then yes / no; yes → the LLM
     reads them into rows = `readme_text` (part 4). Why: an unseen shop may write its list in another format, which
     `readme_list` could not use (the converter parses it with the same regex); it also covers dev_004's prose.
     Risks: a false yes on the 17 READMEs naming a file (`hệ số quy đổi ở ...`; plan_check rejects rows whose product
     is not in the quoted text); a long README for the 9B (fallback: the user's multi-step README decompose).
     **Only the README's last section is shown** (user): from its last header line (any level) to the end
     (`nodes.last_section`, no header words matched). True on all 44 shops: every README ends with `## Quy ước`
     holding every conversion line (dev_013's other hit is an example in a column meaning); 2–12 lines on the 22
     shops that reach the call.
     **Asked one line at a time** (the user's multi-step README decompose, agreed and built 2026-11-12, after the
     9B's "no" on dev_004 in the baseline; user: the private test may hold shops like dev_004, skipping it is too
     dangerous): `nodes.section_lines` splits the section into its items (an indented line joins the item above;
     the header line dropped); per item one call (`prompts.README_LINE`: reason first, then `writes` yes / no). The
     yes lines go to part 4. Trace `readme_writes`: `lines` (per line: prompt, reason, writes, usage), `yes_lines`.
     Cost: 1–11 calls per shop, 117 on the 22 shops without a list. Expected: yes on dev_004's "Hàng theo bó" line
     only. Replaced the one call on the whole section (`README_WRITES`).
     **A shop can need both a file and the README:** dev_004's 3 PVC pipes (`1 bó = 10 cây`) are only in the README
     (the reference hard-codes them, `dev_004.BUNDLE`); its 14 other products only in `DanhMuc.xlsx`. Checked
     without the LLM (fake replies): dev_004 gets both sources.
  2. **Which table** (agreed 2026-11-12; built): **only when part 1's regex found no README list** (user's question;
     no surveyed shop has both). Candidates (code): every table group that is not the line tables (`other_groups`),
     attached or not (dev_013's conversions are a column of its products catalog). One call
     (`prompts.TABLE_CHOICE`): the README's last section (it names the conversion file in 17 shops), per option its
     tables, row count, columns, the README's meanings of them and 3 data rows; reason first, then one enum of the
     option names + `none`. Why one relative choice: no shop has two conversion tables, and an absolute yes / no per
     table failed in `shared_recheck`. No check on the answer alone: part 3's mapping + plan_check's factor-format
     check reject a wrong table. A chosen table goes to part 3; trace `table_choice` (options, prompt, reason,
     option). Checked without the LLM on the saved 9B states: the right table is an option on all 17 table shops;
     biz_110 / dev_009 have no other table (no call). **Risk (user):** a long prompt for the 9B: 1–16 options, up to
     ~13,000 characters (biz_123 / dev_006, sqlite); accepted (the 9B chose right on every shop, 2026-11-12).
  3. **The chosen table's columns** (agreed 2026-11-12; built). Fields of a table source and who decides (user
     asked; the references always read product + unit + factor, the base unit only in dev_002, key always name,
     the format hard-coded per shop):
     - **LLM, one call** (`prompts.TABLE_COLUMNS`): the table's README meanings and a grid of its 6 fullest rows
       (`GRID_ROWS`: dev_013's conversion cell is blank on its first rows); reason first, then four enums of the
       table's columns: `product_column`, `unit_column` (+ `IN_CELL`), `factor_column` ("never a price"),
       `base_unit_column` (+ `NOT_IN_TABLE`; kept as a fallback for shops with no products table: dev_002, 102–104;
       dropped by code when on another role's column or numbers, see "Weak spots of a 9B model").
     - **Code, from the values:** `factor_format` = the first of number / nested / text reading ≥ 90 % of the factor
       cells (`plan_check.factor_format_of`; `text` → `unit_column = None`); `key` = code or name, whichever of the
       line's product values (step 2's product columns, the snapshot's fills, attach's product links) covers more of
       the table's products, ≥ 50 % (`MIN_PRODUCT_MATCH`).
     - Any problem (no format fits, unit in the cell for a number column, two roles on one column, products not
       matched): no source, problems in the trace (`table_columns`); no retry yet.
     - Checked without the LLM (a fake reply with the survey's right columns, saved 9B states): all 15 table shops get
       the right format (dev_005 nested, dev_013 text, the rest number) and key (100 % vs 0 %), and pass plan_check.
       **A price column answered as the factor passes everything** (biz_109 `Giá bán`, dev_006 `GiaDonVi`); a value
       check (qty_base / qty, or line prices) waits for the baseline.
     - plan_check's factor readers made strict for this (`factor_ok`): `nested` refuses a cell with `=` (it read
       dev_013's `1 thùng = 4 bịch`), `text` refuses a bare number.
  4. **README text into rows** (agreed 2026-11-12; built): on the lines part 1 answered yes for. One call
     (`prompts.README_ROWS`) on those lines only: reason first, then a list of rows (product, unit, factor,
     base unit or null), capped at `MAX_ROWS` = 30 (JSON-schema maxItems; an open-length list was padded by the
     27B; max_tokens is the other guard). **Code** takes `text` = the yes lines naming a row's product (the model
     quotes nothing) and checks: ≥ 1 row, factors > 0, every product written in those lines, ≥ 50 % of the products
     among the line's product names (`line_product_keys`, as part 3). Any problem: no source, problems in the trace
     (`readme_rows`); one bad row loses the whole source (strict). Checked without the LLM on dev_004 (fake
     replies): the 3 pipe rows pass and plan_check passes; an invented product, the three pipes in one row, no rows:
     each refused.
     plan_check's `readme_text` check now reads `text` line by line (the lines need not be next to each other).
     Part 4 runs before part 2: dev_004 has both prose and a table.

Earlier notes (when it was postponed):
Finding the conversion source (`conversions` / `conversion_table`: a table, the README list, or none; `column` is
step 2's `qty_base`) is its own step: a conversion table repeats a product per big unit and lists only some products
(fails attach), and gives a factor keyed by (product, unit). Formats differ (3-column tables in biz_117 / 122,
dev_002; nested factors in dev_005; catalog text in dev_013; README lists parsed by `common.readme_conversions`).
Postponed because no later explore step reads it; it matters to the converter (`unit_factor`, `qty_base`,
`base_unit`, `unit` where the line has none). Risk: a missed source gives `unit_factor = 1`, which `schema.validate`
cannot see. Proposed, not discussed: a `run_and_check` check for lines whose `unit` differs from the product's
`base_unit` while `unit_factor = 1`.

## Weak spots of a 9B model (qwen3.5-9b; each marked open or fixed)
Findings from whole runs of the v2 graph on the 9B target model, compared with the 27B. `plan_check` does not see
these: they are wrong meanings, not wrong structure.
- **Step 3 find_anchors (dev_011, dev_014), open:** the 9B does not claim `Ghi tắt` as product_code in `[Ghi tắt]`, so
  there is no code + name pair; `Tên hàng` alone covers too few lines and attach leaves the table out. Lost: the
  `alt_key` join, product_code / product_group, and names for lines written as a short code. Other wrong anchors
  (supplier as customer_name, a date as order_id in dev_014's SoNo / ThuMayCu) are harmless: attach rejects them.
- **Step 2, dev_014, open:** `customer_group ← Khách`, the same raw column as `customer_name`; `shared_recheck` does not
  fix it yet (see step 2).
- **Step 2, dev_012, open:** `product_name` should be `Làm gì` (the repair job); the 9B took `Xe` (the vehicle) or left it
  open.
- **Step 5, words without the known-word list (dev_005), covered by the list:** `Đã giao` → in_delivery (key: completed); with the list on
  (the default), the list covers it.
- **Calls, 429s fixed (user, 2026-11-12):** a 429 once emptied dev_003's statuses (plan_check caught it); fixed by
  the routing over two bf16 hosts (`v2_runner.make_model`, user 2026-11-11: `order` + `only` =
  `primeintellect/bf16`, `parasail/bf16`, fallbacks on, no fp4 / fp8 hosts). The trace does not record which host
  answered. Still true: the steps have no retry of their own, so any other failed call leaves its field empty.
- Options (Claude's, not discussed): code proposing a product_code anchor when a column's values match the line's
  product column.
- **Conversions, part 1's fallback (dev_004):** the 9B answered "no" for the `Hàng theo bó` line ("lists specific
  products sold by the bundle without providing the conversion numbers") though it says `1 bó = 10 cây`; the same
  section also names `DanhMuc.xlsx`, so the "only names the file" case may have pulled the answer. Part 4 never ran;
  the 3 bundle rows are lost. **Fixed (2026-11-12):** the user's multi-step README decompose, one question per line
  of the section (see part 1); on the next 9B run the line got the only yes of 117 lines and its 3 rows were read
  right. Not taken: code proposing candidate lines (`1 <unit> = <n>` outside the list): it assumes the format.
- **Conversions, part 3's base unit (all 6 code-keyed tables: biz_106, 109, 123, 124, dev_003, 006):** these tables
  have no base-unit column, and the 9B always named one anyway: the big-unit column (biz_109, 123, 124, dev_006), the
  factor column (dev_003) or the price column (biz_106 `Giá bán`), often while its reason says "the base unit is not
  present". The answer field contradicts the reason. **Fixed (2026-11-12):** code drops a base-unit answer on a
  column another role holds (as answered) or whose cells read as numbers (`reads_as_numbers`); trace
  `base_unit_dropped`. Replayed on the run's answers and on the next 9B run: the wrong ones dropped (biz_106 named
  `ĐVT` the second time, `Giá bán` the first), the 11 right ones kept. Not taken: asking the base unit in its own
  call.

## write_code (started 2026-11-12; code in `v2/write_code/`)
Turns a `SalesLinePlan` into sales_lines. Recorded here for now (user) until it gets its own file.

*Session 2026-11-12 (write_code):* the plan schema looked at with a filled example (biz_122); the shape agreed (a
fixed no-LLM pipeline + an LLM only filling parameters of predefined functions for leftovers); stages 1–4 built
(`header_rows` added to the plan); stage 5 designed part by part and built, year-less dates included; the step
folders moved under `v2/planner/` by the user (imports updated).

**Next (write_code):**
1. **Stage 6** (rules, see "Stage 6" below): every rule agreed and built (`write_code/rules.py`, runner
   `scripts/write_code/run_stage_6.py`); waiting for the user's run on train + public test.
2. Stage 7 built and run (2026-11-13); open: the no-unit gap of the phone shops (options (a) / (b) under "Stage 7").
   Stage 8 built and benched (2026-11-13). **Next:** the gaps under "Gaps found by the first whole-pipeline
   bench" (order id per row, dev_012 status, the customer-accent decision, `Ghi tắt`). Earlier note: `unit` when the shop has no unit column (biz_128, dev_007: the reference takes the catalog's base unit;
   the phone shops dev_011 / 014: a fixed `cái` / `chiếc`, a known plan gap); conversions → `unit_factor`, `qty_base`.
3. The LLM part (predefined functions, the LLM fills parameters): designed when a shop gives a leftover.
4. Postponed: dev_002 toppings inside the drink's cell (the reference splits them into extra lines); the
   `channel` / `payment_method` plan gap.
- **A fixed pipeline, not an LLM-written converter** (agreed, user 2026-11-12). Why: the plan is one schema of plain
  data for every shop, so a fixed function can apply almost all of it, and a ≤ 9B model cannot reliably write a whole
  `convert()`. Rejected: the original "the LLM writes `convert(shop_dir)` from the plan + reference converters".
  1. **Fixed stages, no LLM in any of them** (user: "make sure every single step in this pipeline doesn't need LLM at
     all"). Built from `common.py`, `parse.py` and the reference converters; measured with `bench_analyst.py`.
  2. **What the fixed stages cannot convert goes to the LLM** (user): preferably a predefined function whose
     parameters the LLM fills; writing code only if that is not enough. Not designed yet.
- **Stages** (Claude's first cut; 1–4 agreed as fixed steps with nothing to decide, user 2026-11-12; the rest not
  discussed yet):
  1. read each line table (header row, skipped rows, subtotal regexes, `source_*`); the same code on every sheet /
     split file, then concat (user);
  2. forward fill `forward_fill_columns` over the concatenated lines;
  3. joins, in plan order (chained);
  4. select and rename: one column per `ColumnSource`, raw values (no parsing yet);
  5. normalize each column into one format per kind (agreed and built; below);
  6. rules: statuses + `blank_status`, return lines, line kinds, non-revenue rows, duplicates;
  7. conversions → `unit_factor`, `qty_base`;
  8. derived columns, `schema.finalize` / `validate`, `bench_analyst.py`.
- **Stage 5, one format per value kind** (user's idea; 2026-11-12). The references already do it through
  `parse.py` (every reference reads dates with `common.dates` → `parse_date`; money `parse_money`; qty `parse_qty`),
  each parser raising on an unknown format.
  - **The kind is fixed by the schema column, not per shop** (stage 4 already renamed the columns): a fixed table
    `schema column → kind → parser` (date: order_date; datetime: order_time; money: unit_price, line_discount,
    line_amount, cost_price; qty: qty, qty_base; qty + unit: `parse_qty_and_unit`; text: codes, names, groups,
    seller, base_unit; customer_name: `strip_phone`; channel / payment: the word lists).
  - **A format outside a parser's list** (user asked whether the schema then fails): no; a new *kind* cannot appear
    without a schema change, only a new *format* of a known kind (`ngày 5 tháng 6`, `1,5 triệu`). The parser raises,
    the cell is a failure, and the hybrid of step 5's status words applies: known formats by code, the rest to the
    LLM (agreed in principle, user; details with the LLM part).
  - **(a) `status` is stage 6, not stage 5** (agreed, user): it is a lookup in the plan's `statuses` /
    `blank_status`, not a format.
  - **(b) `channel` / `payment_method`: build what we know** (agreed, user): the fixed word lists; an unknown word is a
    failure in the report. Why (user): fix it when a shop gives evidence instead of imagining cases.
  - **(c) date + time** (finding, 2026-11-12, from the references and the raw cells): every shop with an
    `order_time` column (biz_108, 117, 128, dev_003, 004, 006, 007, 008) writes a **full date-time** in it (Excel
    datetimes; ISO text in dev_007's csv); every reference reads it with `parse_datetime` alone; no shop has a
    time-only cell (`14:30`). dev_009's `Ngày tháng` mixes `2025-01-01 00:00:00` and `01/01/2025`: the reference
    reads only `order_date` from it (`parse_date` takes the date part) and leaves `order_time` empty. biz_117's text
    cells in `Giờ` are subtotal / note rows (step 0). dev_007's `Ngày giao dịch` mixes `01/02/2025` and Excel
    serials as text (`'45689'`), both read by `parse_date`. **Agreed (user, 2026-11-12):** `order_time =
    parse_datetime(cell)` as the references; a time-only cell raises → failure report (as (b)). The return-line rule
    (`order_time` = the earliest sale line's) is stage 6.
  - **(d) failures** (agreed, user): every failed cell is counted in the report (per column: count, examples, error)
    and the column is still converted (failed cells null). Whether a column goes to the LLM is decided with the LLM
    part.
  - **Built** (2026-11-12, `write_code/normalize.py`, runner `scripts/write_code/run_stage_5.py`): `KINDS`
    (schema column → kind) and `PARSERS` (kind → parse.py function); `normalize(lines, plan)` parses each distinct
    value once, a rejected cell → None + counted (`ColumnReport`: filled, failed, the 5 most frequent failing values
    with count, first row, error); `status` passed through raw. *Built as Claude chose, not discussed yet:* ids /
    codes drop a float's `.0` (`code_text`: `123.0` → `'123'`); qty + unit as `common.parse_qty_and_unit` (the unit
    column wins, else the unit written in the qty cell; when unit and qty are one raw column, the cell's unit); text
    kinds as the references (ids, codes, names, seller → `clean_text`; groups, units → `norm_text`; customer_name →
    `strip_phone`). The runner also compares the format columns (dates, times, money, qty, unit) with the reference
    conversion row by row (matched on `source_*`): a dev check against the answer key, to find wrong-but-successful
    parses; reference values carry stage 6–8 work too (return lines' order_date, implied discounts), so some
    differences are expected. sqlite `source_row`: `common.read_sqlite` numbers the first data row 1, the explore
    tools and the pipeline 2 (row 1 = the column names, as a csv); the runner shifts the reference's.
  - **Year-less dates** (`2/6`, `28/4`: dev_004, dev_013, biz_117; rulebook §8: the year of the sheet the row is
    in). **Agreed (user, 2026-11-12): two code sources checked against
    each other**, (a) a 4-digit year in the sheet name (`T06-2025`, `Tháng 01-2025`; what the references' `sheet_year`
    does) and (b) the year of the full dates in the same sheet (dev_004 mixes `1/1` with `01/01/2025`); the year is
    used where (a) and (b) agree, else the cells stay failures (the LLM's case later: it fills `default_year`).
    Plus a free check: when the sheet name also gives a month (`T06`), the parsed month must equal it, which catches a
    month-first shop without an LLM. **Built** (2026-11-12, `normalize.sheet_contexts` / `parse_date_in_sheet`):
    per (file, sheet) with year-less cells of a `date` column; a year-less cell with no decided year fails with the
    reason (no year in the name, no full dates, full dates not ≥ 90 % one year (`MIN_DATA_YEAR`), name and dates
    disagree); a month mismatch fails the cell. *Built as Claude chose, not discussed yet:* a csv or a sheet whose
    name has no year takes (a) from the **file name** (`BanHang_2025.xlsx`); the name's month = the one number 1–12
    left beside its year; the month check covers year-less cells only (full dates in a month sheet are not checked).
  - **Wrong-but-successful parses** (day / month order: `parse_date` is always day-first; year-less dates whose year
    is in the sheet name, not in the plan; `1.500` as money vs qty). User's idea (not discussed in detail): per kind,
    one small LLM call on the suspicious values decides whether they would parse wrongly; if so the parser is called
    with its other option. Claude's note: code finds the suspicious values (e.g. a date column where no cell has a
    part > 12, so the order is not proven by the values); the LLM only picks the parser's option (a parameter to
    fill, the agreed LLM style). **Postponed until a shop shows one** (Claude's proposal, following the user's rule in
    (b): build on evidence; the user moved on to stage 6). Limit to keep in mind: only the shops with a reference
    conversion can show a wrong-but-successful parse; elsewhere "no failure" means every cell matched a known
    format, not that every value is right.
- **Stage 6, rules** (being designed part by part, 2026-11-12). Five rules: (1) status, (2) return lines, (3) line
  kind, (4) non-revenue rows dropped, (5) duplicate lines dropped (`common.drop_duplicate_lines` on the raw lines,
  after (4), as dev_013's reference and step 6c).
  - **Order inside stage 6** (agreed 2026-11-12): status words + the order-level spread on **all** rows → return
    lines → line kind → drop non-revenue rows → drop duplicates. Why spread first: a non-revenue row is a line of
    the order (§8), so a note or customer written only on a shop's Voucher row is still the order's value; dropping
    first would lose it (dev_013: no note sits only on such a row, so both orders give the same result; its reference
    also reads `note_by_order` on all rows before the drop). The non-revenue rows' own blanks get filled, then they
    are dropped. *Claude's choice, not discussed:* the conflict count is taken on the lines kept after the drops.
  - **Status: one per order** (agreed, user). Each line's word → `plan.statuses` (a word not in the plan: a failure in
    the report, cell null); with an order id, every line of the order takes **its first non-blank word's status**; an
    order with no word on any line gets `blank_status`; no order id: each row alone. Why: rulebook §8 "Ô để trống ở
    các dòng sau của cùng một nhóm dòng … trạng thái của đơn áp cho mọi dòng của đơn … không có nghĩa là … đơn hoàn
    thành" and "mỗi đơn có đúng một trong bốn trạng thái"; a shop writing the order id on every line and the note on
    one line (dev_013 ORD000021: `cancel` on the product line, blank on its Voucher / Phí ship lines) would otherwise
    get two statuses. Rejected: line by line (Claude's first lean: it gave the right numbers on the known shops only
    because rule (4) drops those rows).
  - **Every order-level column is spread the same way, blanks only** (agreed, user). `ORDER_LEVEL` columns (date,
    time, status, customer, channel, payment, seller): with an order id, a **blank** cell takes the order's first
    non-blank value (return lines left out for order_date / order_time: rule 2 sets them). A cell holding a
    **different** value is never overwritten: the order is counted in the report (conflict), as 6a's warning, and
    `schema.validate` flags it too. Why not overwrite (phone_shop's `per_order` does): it cannot tell a spelling
    variant (dev_007 ORD000025 `Ngô Hồng Duy` / `Ngo Hong Duy`, same `KH0016`) from an export error (two dates or two
    statuses in one order), and would hide the second silently. Stage 2's forward fill (`common.forward_fill`, the
    plan's `forward_fill_columns`, continuation lines only) is unchanged; the references and the plan are untouched.
    Evidence (`scripts/checks/check_order_spread.py`, no LLM): after rule (4) nothing is left to fill on the known
    shops, so this is a guard for unseen shops; the references that face the case (phone_shop, dev_013's status)
    spread by order id, none contradicts it.
  - **Return lines, rule (2)** (as the references biz_117 / dev_004; agreed 2026-11-12): `is_return_line = qty < 0`;
    `return_date` = the return line's own date; `order_date` / `order_time` = the earliest of the order's sale lines;
    an order with only return lines keeps `order_date` null (note). **Every line of an order with a return line is
    `returned`, overwriting rule (1)'s status** (user: overwrite on this column only; rulebook §1; the lines whose
    written status said otherwise are counted in a note). **`is_return_line` is computed on every shop, also when
    `plan.return_lines` is false** (user), so a stray negative line on an unseen file shows in the report. Such a
    line is **flagged only** (agreed): `is_return_line = true` and counted in the report; no date or `returned` rule
    on it (a plan / data mismatch to look at; no known shop shows one, so no fix is designed).
  - **Line kind, rule (3)** (agreed): `service_fee` when the line's name is in `plan.service_fee_names`, else
    `product`. The name = stage 4's `product_name` (for code-only shops the products table's name through the join),
    else `product_code`, as step 7 names rows; compared by attach's `value_key` (no accents, case, extra spaces), as
    steps 7 and 6c.
  - **Non-revenue rows, rule (4)** (agreed): a line whose name is in `plan.non_revenue_names` is dropped (same name
    and matching as rule 3), counted in the report. **Duplicates, rule (5)** (agreed): as the references and 6c
    (raw cells of stage 2's lines, lines with an order id, return lines included, after rule 4).
  - **Built** (2026-11-12, `write_code/rules.py`: `apply_rules(lines, raw_lines, plan)` → `Ruled(lines,
    RulesReport)`; runner `scripts/write_code/run_stage_6.py`, which compares status, is_return_line, return_date,
    order_date, order_time, line_kind and the line count with the reference conversion). *Built as Claude chose, not
    discussed yet:* status words matched by the exact stripped text, then `value_key`; a failed status cell takes no
    part in the spread and gets no `blank_status`; return lines without an order id keep their written date as
    `order_date` (counted); rule 2 changes only the return lines' dates, the sale lines keep theirs (the references
    set every line to the earliest sale date; the same wherever an order's sale lines agree); conflicts compared by
    `value_key` (a name with and without accents is not a conflict; nicknames like `Chú Quân` / `Lý Hoàng Quân` are).
    **Gap:** a shop with no status column (dev_012) gets no `status` at all; the reference sets completed (step 5a's
    open point: the plan has no `blank_status` for it). The runner calls the
    stages one after another; `pipeline.py` still holds stages 1–4 only.
  - *Not discussed yet:* dev_014 (no customer codes) keeps both spellings of a name within an order; whether
    `customer_name_key` folds accents decides if `customer_key` splits that customer in two.
  - *Not discussed yet (explore step 5):* the blank block tells the LLM "each of these rows has its order id written,
    so the blank is not a later line of an order repeating the order's first line"; false for a shop writing the
    order id on every line (dev_013's Voucher / Phí ship lines are counted as leftover blanks). The answer
    (`completed`) was still right there.
- **Stage 7, units and conversions** (being designed, 2026-11-13). Four cases by what the line has (references):
  A `qty_base` column; B `unit` without `qty_base`; C `qty_base` without a unit column (biz_128, dev_007: the unit
  found by (product, factor)); D neither (phone shops: a fixed `cái` / `chiếc`, a plan gap). All sources of
  `plan.conversions` parsed into one lookup (product, big unit) → factor.
  - **Case A** (agreed, user): `unit_factor = qty_base / qty` from the line; the conversion sources only cross-check
    it (disagreeing lines counted in the report), as every reference.
  - **No LLM in stage 7** (user: "proceed with every suggestion"): every step is a lookup or arithmetic on what the
    plan says. A line it cannot decide gets a null factor (or unit) and a counted failure, for the LLM part later
    (unit spellings that differ from the source, unmatched product names, no base unit, two big units with one
    factor in case C).
  - **Case B** (agreed): 1 when the unit is the base unit, else the lookup's factor; neither → null, a counted
    failure, never guessed as 1.
  - **Built** (2026-11-13, `write_code/units.py`: `apply_units(lines, shop_dir, plan)` → `Unitized(lines,
    UnitsReport)`; runner `scripts/write_code/run_stage_7.py`, compares unit, base_unit, unit_factor, qty_base with
    the reference). *Built as Claude chose, not discussed yet:* one lookup (product, unit) → (n, of unit) for every
    source; nested / text factors written in another big unit resolved through it; products by `value_key` (code
    when the line's code is in a code-keyed source, else name), units by `norm_text` (accents kept: `bó` ≠ `bộ`);
    `base_unit` = the line's (joined products table), else a source's; a product in no source with no known base
    unit takes its line unit as base (as dev_002's reference); case D: a product with no big unit in any source has
    one unit, so factor 1 (unit = base_unit when known), else a failure; case C with factor 1 and no base unit:
    unit null, counted.
  - **Same code, same product** (agreed 2026-11-13, user): a line whose name matches no name-keyed source takes the
    match of the other lines with its product code, when they all matched one source product; a code matching
    several is left alone (counted). Why: biz_119 / biz_120 write some lines `Amlodipin 5 mg …`, the README list
    `Amlodipin 5mg …`; the code (`SP0027`) is the product (`product_key`), so no spelling is guessed.
  - **No qty column → no `unit_factor`** (agreed 2026-11-13, user; dev_009 orders only, dev_012 repair tickets):
    a factor means nothing without a quantity (the references leave it empty).
  - **Gap: no unit anywhere (phone shops dev_011, dev_014)** (open, options not decided; recorded 2026-11-13, user).
    Stage 7 leaves `unit` / `base_unit` empty; the reference hard-codes `chiếc` (phones) / `cái` (accessories).
    - Effect: rulebook §3 "Bán được bao nhiêu <đơn vị>" sums `qty_base` of the products **whose base unit is**
      <đơn vị>, so a unit question over several products (query_agent: `filters.base_unit`) matches nothing and is
      likely answered 0 (−1). Public test: ~3 of the 103 dev_011 / dev_014 questions ("mấy chiếc điện thoại, gộp cả
      máy mới với máy cũ", "Máy cũ … mấy chiếc", "bao nhiêu máy không tính phụ kiện"). Questions on one product
      ("iPhone 13 … mấy chiếc", "Ốp lưng … bnhiu cái"), bán chạy and price-effect questions are not affected
      (`qty_base` of the product; factor 1).
    - Rulebook: §7 "Thông tin file không ghi thì không suy ra từ cột khác, trừ khi rulebook ghi cách lấy"; no rule
      says how to get a unit. dev_011's README writes it once ("**Giá**: đơn giá một máy / một cái"); dev_014's
      README never does (the reference's words there are common sense).
    - **(a) Explore step reads the unit:** when no unit exists anywhere (step 4's known gap), one small LLM call
      per product group (`máy mới`, `máy cũ`, `phụ kiện`): what one item is counted in, from the README → a new plan
      field (base unit per product group) that stage 7 applies by code. Works where the README says it (dev_011);
      where it is silent (dev_014) the model answers from general knowledge, which §7 arguably forbids.
    - **(b) Query-agent side:** a shop that records no unit counts items; a unit question there becomes "sum the
      quantity of the products the noun names" (`điện thoại` → the phone groups, `máy cũ` → that group), the unit
      word meaning pieces. Nothing invented in the data (§7 holds); a rule in the query_agent, not the converter
      (Claude's lean).
- **Stage 8, derived columns and finishing** (being designed, 2026-11-13). Parts: (1) product identity, (2)
  `customer_key` = the customer code, else `customer_name_key(name)` (every reference; rulebook §3), (3)
  `line_discount` when no column holds it = `qty × unit_price − line_amount` (`implied_discount`, every reference),
  (4) `finalize` / `validate`, write `sales_lines.parquet` + `manifest.json` to `data_std_agent/`, bench. The bench
  compares the sets of `product_key` / `customer_key` with the reference, so keys are built as the references do.
  - **Product identity, part 1** (agreed, user: "do the same as the reference"; rulebook §8 "File có mã thì theo
    mã", §3 spellings of one name are one product): `product_key` = the product code (on the line or through the
    products table); no code + a products table joined by name: the joined row's spelling for `product_name`, key =
    its `norm_text` (as `match_names`; stage 3's row pointer says which row); no code, no table: the most frequent
    spelling among those differing only in accents (`canonical_names`), key = its `norm_text`. With a code the line
    keeps its own name.
  - **Part 4** (agreed): `data_end` from the README's `Dữ liệu có đến hết dd/mm/yyyy` (`readme_data_end`, as the
    references), else the last `order_date` with a warning; **no `brief`** (the references' briefs are hand-written
    per shop; a brief step, if needed, is a later explore step); `sources` = line tables, joined tables, conversion
    tables, `README.md`.
  - **Built** (2026-11-13, `write_code/finish.py`: `finish(lines, plan, joined, shop_dir)` → `Finished`, `write`;
    runner `scripts/write_code/run_stage_8.py` runs stages 1–8 and writes `data_std_agent/<split>/<shop>/`; then
    `scripts/bench_analyst.py --split <split> --candidate data_std_agent`). *Built as Claude chose, not discussed
    yet:* the products table "joined on the line's name column" = a join from a line table whose `on_key` is
    step 2's `product_name` column, its name column = `alt_key` for a code + name pair (attach's `PAIRS`: code
    first), else `key`; no-code lines that join no row take the most frequent spelling among themselves;
    `customer_key` by code when the shop's lines carry any customer code; `line_discount` implied only when no
    column is mapped and qty, unit_price, line_amount all are.
- **Gaps found by the first whole-pipeline bench** (2026-11-13, 9B plans; recorded, none decided yet; suggested
  order: 1, 2, then 3 needs the user's call, 4 stays with the 9B weak spots):
  1. **No order id → no `order_id`** (dev_012, biz_107, 113, 126). The query_agent counts orders as distinct
     `order_id`, so every "bao nhiêu đơn / phiếu" there is 0. Rulebook §8 "Bảng không ghi theo đơn: mỗi dòng là một
     lần ghi"; Sửa xe "mỗi dòng sổ sửa xe là một lượt xe (một phiếu)". *Proposal (Claude's, not discussed):* stage 8
     builds `order_id` = sheet + row when no order id is mapped, as dev_012's reference (`T1-2026#10`); code only.
  2. **No status column → no `status`** (dev_012; step 5a's open point). Revenue counts only completed /
     in_delivery lines, so every revenue measure on such a shop is 0. Rulebook §8 "Sổ không có cột trạng thái …
     Ghi chú không nhắc trạng thái là đơn hoàn thành". *Proposal (not discussed):* step 5 asks its blank question
     also when there is no status column (the plan gets `blank_status`), and stage 6 gives it to every line.
  3. **Customer names differing only by accents** (dev_014: 13 extra `customer_key`, 69 orders with two keys).
     Lines of one order write `Đào Bình Hoàng` / `Dao Binh Hoang`; `customer_name_key` keeps accents and stage 6
     fills blanks only (agreed). The reference hides it inside an order (phone_shop's `per_order` overwrites), but
     its own keys still hold 61 pairs differing only by accents. Rulebook §3: names differing only in spelling are
     one customer. *Options (not discussed):* (a) fold accents in the name key (e.g. the most frequent spelling per
     accent-folded name, as products): follows §3 more closely than the reference, the bench then disagrees with the
     reference on those pairs; (b) keep matching the reference.
  4. **dev_011 / dev_014 `Ghi tắt` not resolved** (the 9B weak spot of step 3: the abbreviation column is not
     claimed, so no code + name join): `CL 9D` and `Kính cường lực 9D` count as two products, `product_group` (máy
     mới / máy cũ / phụ kiện) is missing. Costs product and group questions there; the fix is in the explore step.
  - No effect on answers (left as they are): `line_no` not mapped (step 2, user's decision); dev_009
    `line_discount` empty (no qty column; the reference writes 0); dev_011 / dev_014 order ids `1` vs the
    reference's `STT1`; dev_002 toppings (postponed); the phone shops' unit (see "Stage 7").
- **Plan gap found:** `channel` / `payment_method` have no word mapping in the plan (only `parse.map_channel` /
  `map_payment`'s fixed lists); a new word on an unseen shop becomes None.

### Stages 1–4 (built: `write_code/tables.py`, `joins.py`, `pipeline.py`; runner `scripts/write_code/run_stages_1_4.py`)
- **`SalesLinePlan.header_rows`** (new plan field, from `state["header_rows"]`, filled by `write_plan`): the plan
  named tables but not their header rows (confirm_headers' answers lived only in the state). Tables are read with
  `explore.tables(shop_dir, file, sheet, plan.header_rows, plan.skipped_rows)`, so the pipeline sees exactly the
  rows, row numbers and column names the explore steps saw (README header > confirmed > guess, same code).
- *Built as Claude chose, not discussed yet:*
  - rows: the table's data rows (`data_numbers`: below the header, minus `skipped_rows`), blank rows dropped (as
    `common.read_xlsx`), then line-table rows whose `row_text` matches a `subtotal_labels` regex (case-insensitive;
    on the plan's own data these are already in `skipped_rows`, the regex is for rows it never saw). A repeated
    column name keeps the first; later ones become `<name>#<k>`.
  - forward fill = `common.forward_fill` (continuation lines only, from their own order's first line).
  - joins keep each table as its own frame; a join gives every line a **row pointer** into the joined table (chained
    joins follow the pointer of their `on_file`), so no column names collide. Values compared with attach's
    `value_key` (no accents, case, extra spaces), as attach proved the link; `alt_key` indexed too. A key value on
    several rows: the first row wins, counted in the report (attach allows 1 % repeats). A join's `on_file` that is
    any line table means the concatenated lines.
  - stage 4 output: one column per schema column with raw cells, plus `source_*`; the raw lines (after forward
    fill) are kept beside it for stage 6's duplicate rule (identical in every raw cell).
  - a joined group spread over several files is read from its first table only (as `write_plan` names it).

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
  marked ✓ / ✗ / ? against `parse.map_status`, leftover blanks → `blank_status`), `run_line_kinds_v2.py` (step 7 →
  `-v2-line-kinds/`), `run_cleaning_inputs_v2.py` (no LLM; 6a–6c on any state after step 2, after step 7 for the
  not-revenue skip → `-v2-cleaning/`), `run_conversions_v2.py` (map_conversions on any state after prepare →
  `-v2-conversions/`), `run_write_plan_v2.py` (no LLM; step 8 → `-v2-plan/`).
- Checks (`scripts/checks/`, no LLM): `check_plan_check.py`, `check_v2_line_tables.py` (step 1), `check_anchors.py`
  (step 3 by values), `check_filter.py` (step 0 vs the `SUBTOTAL` answer key + a fake model), `check_readme_headers.py`,
  `check_shared_columns.py <run folder>...` (llm_check / snapshot shared-column checks replayed on saved runs),
  `check_status_prompts.py` (outdated: rebuilds step 5's old one-call prompt).
- Prompt experiments on saved states (call the model, load `.env`; nothing written to a state):
  `scripts/explore/try_line_kinds.py` (step 7 variants), `zalo_main.py` (scratch, project root: `--pass2` step 2's
  `open_fields`, `--filter-only` its code filter without a model, `--blank` step 5's blank call, default mode
  llm_check with an unused-columns block).

## Code layout (under `src/datamate/analyst_agent/explore_agent/`)
LangGraph layout: a folder that builds a graph has `agent.py`, with `nodes.py`, `state.py`, `prompts.py` beside it.
```
explore_agent/
├── explore.py        tools as plain functions (list_files, table_groups, tables, peek, column_values, find_rows,
│                     column_index); Table.skipped + data_numbers() / data_rows()
├── readme.py         README column lists (readme_columns)
├── plan_check.py     MAPPABLE, OPTIONAL, DERIVED, ALWAYS_RECORDED, check_plan
├── state.py          SalesLinePlan (+ TableRef, ColumnSource, Join, StatusWord, Status, ConversionSource,
│                     ConversionRow, ExtraAttribute),
│                     ExploreContext (model, rulebook), ShopState
├── nodes.py          confirm_headers, prepare
├── utils.py          rulebook_sections, usage, since, label
└── v2/               the workflow
    ├── agent.py          NODES, build_graph, explore_agent + the partial graphs
    ├── state.py          WorkflowState: skipped_rows, subtotal_labels (0); line_tables (1); columns, open_columns,
    │                     column_problems (2); anchor_tables, attached, dropped_claims, min_unique, fields_to_fill,
    │                     snapshots (3); missing_columns (4); statuses, blank_status, known_status_words (5);
    │                     forward_fill_columns, return_lines, notes (6); service_fee_names, non_revenue_names (7);
    │                     conversions (not built); plan, plan_problems (8)
    ├── utils.py          call_structured, dump_state / load_state
    ├── planner/          the explore steps (0-8) that write the SalesLinePlan
    │   ├── filter_rows/      step 0: rows.py (find_rows, pattern_key, pattern_regex, row_text, DENSE), nodes.py
    │   │                     (filter_rows, filter_group, verdict_schema), prompts.py
    │   ├── find_line_tables/ step 1: nodes.py (choose_line_tables, option_names, line_table_candidates), prompts.py
    │   ├── column_mapping/   step 2: agent.py (subgraph + map_line_columns), nodes.py (answer, check, llm_check,
    │   │                     shared_recheck, open_fields + OPEN_FIELDS / open_field_candidates, ASKED,
    │   │                     line_columns_schema, line_tables_of, table_text, readme_meanings_text), checks.py,
    │   │                     amounts.py, state.py, prompts.py (COLUMN_MEANINGS, NOT_HERE, ...)
    │   ├── fill_missing_schema/ step 3: anchors.py (ANCHOR_MAP), nodes.py (fields_to_fill, named_groups,
    │   │                     other_groups, table_snapshot), find_anchors/, attach.py, anchor_resolve/, snapshot/
    │   ├── missing_columns/  step 4: nodes.py (report_missing, KNOWN_GAPS)
    │   ├── status_words/     step 5: words.py (status_column, status_words, leftover blanks), nodes.py (map_statuses,
    │   │                     KNOWN, status_message, status_schema), prompts.py (STATUS_WORDS, MEANINGS, blocks)
    │   ├── cleaning_inputs/  step 6: return_lines.py (return_lines, problems), forward_fill.py (ORDER_LEVEL,
    │   │                     forward_fill, problems), duplicates.py (duplicates, row_name, note), nodes.py
    │   │                     (cleaning_inputs: 6b, then 6a with 6b's rows, then 6c without step 7's not-revenue rows)
    │   ├── line_kinds/       step 7: candidates.py (find_candidates, TOP_K, UNUSUAL), nodes.py (map_line_kinds,
    │   │                     rule_rows, line_kinds_schema), prompts.py (LINE_KINDS, KINDS, RULE_ROWS)
    │   ├── conversions/      map_conversions: nodes.py (map_conversions, readme_sources, last_section, section_lines,
    │   │                     writes_schema,
    │   │                     choose_table, table_option_text, table_schema, map_table, columns_schema,
    │   │                     line_product_keys, grid_rows, readme_rows, rows_schema), prompts.py (README_LINE,
    │   │                     TABLE_CHOICE, TABLE_COLUMNS, README_ROWS)
    │   └── write_plan/       step 8: nodes.py (write_plan, build_plan, table_refs, CONVERSIONS_NOTE), unmapped.py
    │                         (unmapped, what_it_holds)
    └── write_code/       plan → sales_lines: tables.py (stage 1: read_table, read_line_tables), joins.py (stage 3:
                          apply_joins, row pointers), pipeline.py (stages 1–4: apply_stages_1_4, Applied),
                          normalize.py (stage 5: KINDS, PARSERS, normalize, ColumnReport), rules.py (stage 6:
                          apply_rules, RulesReport), units.py (stage 7: apply_units, Lookup, UnitsReport),
                          finish.py (stage 8: finish, write, FinishReport)
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
- Run results go in the reply, not in this file or `problem.md`; a model's weak spots are the exception (written
  as findings, user 2026-11-11).

## Environment
- Windows. Python: `.venv/Scripts/python.exe -I` (pandas, openpyxl, pyarrow, langgraph, langchain; uv project, the
  venv has no pip: ask the user to install a missing package).
- `-I` drops the project root from `sys.path`: scripts insert it before `from src.datamate ...`. The console is
  cp1252: a script printing Vietnamese calls `sys.stdout.reconfigure(encoding="utf-8")`.
- Bash output: redirect to `./cmd_output.txt` (project root) and read it back; `/tmp` does not work.
- Structured output: `with_structured_output(..., method="json_schema")`.
- Generated data: `data_std/<split>/<shop>/` (references, never hand-edited); agent output `data_std_agent/`.
