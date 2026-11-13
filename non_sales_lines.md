# Non-sales_lines file types (group B: event tables): handoff notes

Started 2026-11-13. This file is meant to be read **on its own**. It carries what's needed from `problem.md`, the long project history. Open `problem.md` only for details of the existing sales_lines work, and `explore_agent.md` for that agent's internals.

## The task
ZaloAI "DataMate" challenge: an LLM agent answers Vietnamese business questions about small shops' sales data.
- Each question is one line of `questions.jsonl`: `{"id", "business_id", "question", "as_of_date"}`. `business_id` is the shop folder `<split>/data/<business_id>/`, which holds xlsx / csv / sqlite files and a `README.md` describing that shop's files, columns, unit conversions and conventions. READMEs differ per shop. Lists may be split across files (`_1`, `_2`, `_2025`) or sheets. `as_of_date` is "today" for the question.
- `<split>/rulebook.md` defines every term and the **exact answer format** (§9). Read the relevant sections before designing a measure; the answers depend on it. The public-test rulebook differs from train: it adds industry rules (mẹ và bé, sửa xe, phone shops, café toppings) and drops a few rules.
- `<split>/calendar.csv`: lunar calendar (Tết questions).
- Splits: `dataset/zaloAI/train/` (3000 questions, `answers.jsonl` key, shops `biz_101`–`biz_130`) and `dataset/zaloAI/public_test/` (1000 questions, no key, shops `dev_001`–`dev_014`, scored only on the competition website). The final test has **unseen shops**, so nothing may be hard-coded per shop.
- Answer key line: `{"id", "type", "answer", "alias"}`. Types: number, percent, list, date, string, unanswerable, boolean, table. A submission line is `{"id", "type", "answer"}`, unanswerable is `{"id", "type": "unanswerable", "reason": "..."}`, abstain is `{"type": "abstain"}`.
- **Scoring: +1 right, −1 wrong, 0 abstain.** A wrong format counts as wrong (money is an integer, percent is `12.4` not `0.124`). So when unsure, abstain.
- §7 unanswerable: the released data is not enough. That means the period lies **entirely** outside the shop's data range, or the shop does not record the kind of information asked for. It is not 0, and nothing may be inferred from other columns unless the rulebook says how. A period partly inside the data is computed on the part that has data.

## Constraints and working rules
- **Model limit (competition rule):** all models used at inference (LLMs, embedders, rerankers, classifiers) total **≤ 12B parameters**. The LLM target is about 9B (development runs used qwen3.5-9b and Qwen3.8-27B). Keep every LLM job small: one question at a time, a one-sentence reason before the answer, enum answers, fixed steps in code wherever code can do the work. A long or mixed prompt makes a 9B model miss things.
- **Workflow over agent:** fixed LangGraph steps where code fetches exactly the data needed, the LLM answers one small constrained question (structured output, `Literal` enums, `method="json_schema"`), and code checks the answer straight away. Use an agent loop only for genuinely flexible steps.
- **No hard-coded shop lists or values** from the 44 known shops (unseen shops write things differently). Known lists such as `parse.STATUS_MAP` and the `SUBTOTAL` regex are answer keys for checking, not inputs.
- Design ideas are labelled **agreed / not discussed yet / rejected** as the user decides them, one at a time.
- **The user runs the live runs and the check scripts.** Give the commands and wait for the report; don't rerun checks after every change. Run results go in the reply, not in this file (this file holds decisions, designs and reasons).

## Environment
- Windows. Python: `.venv/Scripts/python.exe` (uv project; no pip in the venv; pandas, openpyxl, pyarrow, langgraph, langchain-anthropic installed). If a package is missing, ask the user to install it.
- Run code that reads dataset files with `-I`. `-I` drops the project root from `sys.path`, so scripts in `scripts/` insert it themselves before `from src.datamate ...` (see `scripts/convert_reference.py`). `-I` also ignores `PYTHONIOENCODING`, so a script that prints Vietnamese must call `sys.stdout.reconfigure(encoding="utf-8")` (the console is cp1252).
- To capture shell output, redirect to `./cmd_output.txt 2>&1` in the project root and read it back with the Read tool (`/tmp` does not work).
- **Never read `.env`.** Ask before running anything that loads it (`zalo_main.py`, `scripts/run_agent.py`, the `scripts/explore/` runners that call a model).

## What exists already (sales_lines)
The system is **two agents** over standard tables. So far only `sales_lines` exists.
- **query_agent** (`src/datamate/query_agent/`, built): answers a question by calling tools over the shop's converted tables. It never reads raw files.
  - `find_entity`: fuzzy text → exact product / customer key.
  - `information_finder`: **measure-based**. The agent names a measure (`revenue`, `order_count`, `cancel_rate`, `gross_profit`, ...) and the tool applies the rulebook's row selection. Shared arguments: `period` (resolved in `periods.py`: day, this_month, quarter, range, tet, ...), `filters`, `group_by`, `top_n`, `compare`. It returns a short text and stores the full frame under a handle `r1`, `r2`, ....
  - `calculate`: safe expressions over handles and scalars (`share(r1, r2)`, `count(setdiff(r1.product_key, r2.product_key))`). It only sees aggregated results, never lines.
  - Public test score 0.769 with hand-converted data. Its handoff file is `query_agent.md`. Another developer adapted it to a 9B model with a router to task families, one task tool per family. New file types will become new families or tools there.
  - Today a question that needs non-sales data is answered `OUT OF SCOPE` → abstain. About 170 of the 217 public-test abstains are event-table questions: the prize of this work.
- **analyst_agent** (`src/datamate/analyst_agent/`): converts an unseen shop folder into `sales_lines.parquet` + `manifest.json`, once per shop, offline. Shape: `explore_agent` (a fixed workflow that writes a `SalesLinePlan`: header rows, skipped subtotal rows, line tables, column mapping, joins, status words, line kinds, forward fill, return lines, unit conversions) → `write_code` (a fixed no-LLM pipeline that applies the plan) → `run_and_check` (not built). Details in `explore_agent.md`.
- **Hand-written reference converters** (`src/datamate/reference/biz_<id>.py`, `dev_<id>.py`; 5 train shops: 108, 116, 117, 122, 128; plus all 14 public-test shops) are the ground truth. `scripts/convert_reference.py [--split public_test] [shop ...]` writes `data_std/<split>/<shop>/`. Agent output goes to `data_std_agent/<split>/<shop>/` and is compared with `scripts/bench_analyst.py` (`src/datamate/bench.py`).

### sales_lines schema (short)
One row = one product line (or service-fee line) of an order, after cleaning. Order-level and product-level fields are copied onto every line, so tools never join. Every column may be null; the manifest lists which columns the shop records.
`order_id`, `line_no`, `order_date` (periods go by order date), `order_time`, `status` (`completed` / `cancelled` / `returned` / `in_delivery`), `customer_key` (code, else normalized name; null for walk-in), `customer_name`, `customer_group`, `channel` (`store` / `zalo_oa` / `phone` / `ecommerce`), `payment_method` (`cash` / `transfer` / `ewallet` / `cod` / `debt`), `seller`, `product_key` (code, else normalized name), `product_code`, `product_name`, `product_group`, `line_kind` (`product` / `service_fee`), `is_return_line`, `unit`, `qty`, `base_unit`, `unit_factor`, `qty_base`, `return_date`, `unit_price` (before line discount), `line_discount`, `line_amount` (after line discount; the unit of revenue), `cost_price` (per sale unit, as written on the line), `source_file`, `source_sheet`, `source_row`.
Revenue (§1) counts completed + in_delivery orders. Product identity is by code, else by name (spellings of one name are one product, matched accent-insensitively).

### Shared code to reuse
- `src/datamate/parse.py`: cell parsers and maps: `is_blank`, `clean_text` (None for blanks, never the text `"nan"`), `norm_text`, `fold_accents`, `parse_money` (`13,353,000`, `55.000đ`, `25k`, `1tr440`, `16,56tr`, negatives), `parse_qty`, `parse_date` (dd/mm/yyyy, Excel serials, `1 thg 6 2025`, ISO, datetimes), `parse_datetime`, `strip_phone`, `customer_name_key`, `map_status`, `map_channel`, `map_payment`, `WALK_IN_NAMES`.
- `src/datamate/reference/common.py`: `read_xlsx`, `read_csv`, `read_sqlite`, `drop_subtotals`, `drop_repeated_headers`, `forward_fill`, `drop_duplicate_lines`, `clean_line_table`, `readme_data_end`, `readme_conversions`, `conversion_lookup`, `match_names`, `canonical_names`, `parse_qty_and_unit`, `Converted`.
- `src/datamate/schema.py`: `SALES_LINES_COLUMNS`, `finalize`, `validate` (types, enums, required columns, no `nan` / `None` text), `manifest`, `write`. New file types need the same per-type pieces.
- `src/datamate/analyst_agent/explore_agent/explore.py`: explore functions over a shop (`list_files` grouping same-header tables, `peek`, `column_values`, `find_rows`, header detection from the README); `readme.py` (column lists from the README: `## Cột trong \`File\`` + `- **Col**: meaning`).
- Survey scripts: `scripts/survey_peek.py`, `scripts/survey_values.py` (per column: distinct values or format mix); saved output in `survey_values_all.txt` (train) and `survey_values_test.txt` (public test). `scripts/checks/dump_headers.py` (headers of every file → JSON) + `sales_lines_report.py` (JSON → `survey/sales_lines_columns.md`; the same pair can produce the listing for other file types).

### Design rules that carry over
- **Lookup table or event table?** Lookup tables (product catalog, customers, staff, drug catalog) describe something a sales line already refers to, so they are joined into `sales_lines` columns at conversion time (group A, `extra_attributes`, later). Event tables (imports, payments, stock, lots, deliveries, expenses) have their own dated rows with amounts, so each becomes its own file type with its own schema and finder. **This file covers the event tables.**
- **A tool is defined by the table it reads:** one finder per data source (`sales_finder` = today's `information_finder`, `debt_finder`, import / stock finders), not one per measure family and not one per rulebook section. Periods and rankings (§2, §3) stay shared arguments (`periods.py`).
- **A rulebook formula is a measure, not a `calculate` job.** `calculate` cannot multiply per line or subtract two grouped results key by key, and a 9B model should not assemble formulas. Combining two finished answers (imports this month vs revenue) is fine in `calculate`.
- **Missing data must be representable.** The manifest says which file types and columns a shop records, so a question about data the shop truly lacks becomes a confident `unanswerable` (+1) instead of an abstain. It may only say "missing" when the converter really checked, never because something wasn't mapped (wrong OUT OF SCOPE notes cost ~20 public-test questions).
- Cleaning seen in the raw files, which applies to event files too: header offsets (title rows, filter rows, two-level headers), month-per-sheet files sharing one header, subtotal / footer rows inside the data (`Tổng ngày`, `Cộng`, `Tổng tháng`), forward fill (order-level cells only on the first line), duplicate rows, mixed date formats, money written as text, quantity with the unit inside (`3 chai`), names spelled differently across files (`PEPSI 330ML`; join on code or normalized name).

## Why this work
`sales_lines` covers about 70% of the questions. Most of the rest need **event tables**. Lookup attributes (drug active ingredient / prescription, dev_013 brand / size, dev_012 vehicle model, customer gender / address) are **not** in scope here: they are group A, to come back to later, since most of their machinery is already built.

## The event-table files found in the train survey
| File type | Row = | Train shops |
|---|---|---|
| orders | one order: total after order discount, paid, debt, channel, payment method, seller, created time | separate file in 106, 108, 109, 110, 111, 119, 120, 123, 124, 127, 128, 129; elsewhere order fields repeat on every sales line |
| payments | one payment against an order | 106, 109, 111, 123, 124, 127, 129 |
| debt_terms | due date per order | 115–118 |
| imports | supplier receipt header + lines | 105, 106, 108, 109, 117, 119–124, 128 |
| stock | stock on the last data day (base unit) | 105, 106, 108, 109, 117, 119, 120, 124, 128 (123: stock ledger `the_kho`) |
| lots | lot, expiry, qty sold / returned per lot | 119–124 (pharmacies) |
| price_history | price changes | 105, 117, 123, 124 |
| deliveries | delivery runs | 115–118 |
| one-offs | expenses (124), tables / areas (128), returns + staff (123) | |

Known gaps: 121 and 122 have import receipts **without any money** (quantities and lots only), so import-money questions there are unanswerable. 118 has no stock file (stock questions unanswerable).

Public test (from the abstain analysis): debt / thu nợ / bán chịu ~48, stock / lots / expiry ~18, imports / NCC ~15, phone-shop trade-ins / instalments / warranty ~26, dev_004 delivery runs ~8, expenses (lãi ròng) a few.

## Question counts (rough keyword buckets, 2026-11-13)
| Type | Train (3000) | Public test (1000) | Train shops with the files |
|---|---|---|---|
| stock / lots / expiry | ~104 | ~19 | 105, 106, 108, 109, 117, 119–124, 128 |
| debt / payments / instalments | ~90 | ~44 | 106, 109, 111, 115–118, 123, 124, 127, 129 |
| imports | ~87 | ~14 | 105, 106, 108, 109, 117, 119–124, 128 |
| deliveries | ~5 | ~1 | 115–118 |
| expenses (lãi ròng) | ~2 | ~2 | 124 |

Keyword matching only; to be replaced by a proper tagging of the train questions.

## Rulebook rules for these types (train rulebook, paraphrased; check the split's own rulebook)
**§4 Công nợ (debt)**
- Orders counted in debt: not cancelled; a returned order still counts if it still has debt.
- Debt of one order at day X: the unpaid part of the **order total** (what the customer owes for the whole order: after the order-level discount, **including delivery fee**), counting payments up to the end of day X. Outstanding debt = sum over orders with debt > 0.
- Debt by customer: customer identified as in §3. Walk-in orders count in the **total** debt but have no row in a per-customer table.
- COD orders and marketplace (sàn TMĐT) orders are **not customer debt**, even if unpaid; left out of debt and debt age.
- Debt age: days from the **order date** (not the due date). Ordered today = 0 days, yesterday = 1.
- New debt, debt paid, debt collected in a period: only **ghi nợ** (on-credit) orders, not cancelled. **New debt**: the part of the order total not paid on the purchase day, for orders placed in the period. **Debt paid / collected**: payments made **after the purchase day** with a payment date in the period, for every on-credit order (including earlier orders and returned orders). A payment on the purchase day counts as paid at purchase. Refunds for returns are not subtracted.

**§5 Kho, nhập hàng, giá vốn (stock, imports, cost)**
- Import money in a period: sum of the receipts whose **receipt date** is in the period, cancelled receipts left out. Opening-stock receipts / lines (nhập đầu kỳ) count like any other receipt. Top supplier = highest import money.
- Imports minus sales: the sold part is the quantity of the orders counted in revenue (§1), converted to the import's unit.
- Average import price of a product in a month: **weighted by imported quantity** (not the mean of prices), per import unit, receipts dated in the month, cancelled left out.
- Stock in big units: a list `[whole big units, leftover base units]` (second number 0 when none). Multi-level units: the leftover stays in base units.
- Stock value: stock at **end of today** for each product × the **cost per base unit in the product catalog**; only products still in stock.
- Out of stock at end of day: products **bought to resell** (not drinks / dishes made on site) whose stock is 0 at the **end** of that day; running out mid-day and restocking before the end does not count.
- Cost (giá vốn): weighted average over import lots at the time of sale (not FIFO, not last import price). Gross profit uses the cost written on the sale line (already built as `gross_profit`).
- Net profit in a month: gross profit − **every** expense in that month's expense book.

**§6 industry rules touching these types**
- Pharmacy: expiring in the next N days = at today D, lots still in stock with expiry from D to the end of D + N; their **capital** = value of the lot's remaining stock at **that lot's own import price** per base unit (not the average cost). The lot closest to expiry of a drug = among lots still in stock and not yet expired today, the earliest expiry.
- Building materials: number of delivery runs in month X = runs with a delivery date in X; each run of each order counts once, including runs of orders later returned.

## Order of the file types (agreed 2026-11-13)
**imports → debt → stock → deliveries.**
- **Imports first**: an import receipt line has nearly the shape of a sales line (receipt id, date, supplier, product, unit, qty, unit price, amount, cancelled or not), so most of the sales_lines machinery carries over (header finding, row filtering, column mapping, product joins, unit conversions, money / date parsers). The §5 rules for it are simple. Stock also needs it (below).
- **Debt second**: the biggest public-test bucket, but harder: §4 asks for balances at the end of a day X (outstanding debt, number of debtors, debt age) and for flows in a period (new debt, debt collected), with COD / marketplace orders excluded and same-day payments counted as paid at purchase.
- **Stock third**: shops record stock only on their last data day, so stock at the end of an earlier day X = final stock − imports after X + sales after X. It needs imports and `sales_lines` first.
- **Deliveries last** (or skipped): very few questions.

## Tables per type (proposed, not settled)
The schemas come out of each type's survey; this is only what is expected so far.
- **imports**: one table of receipt lines, with the receipt's date, supplier and status on every line.
- **debt**: probably two tables:
  - `orders`: one row per order with what §4 needs (order total after the order discount and including shipping, paid at purchase, payment method, due date). Always produced, derived from `sales_lines` when the shop has no orders file (proposed earlier in `problem.md`).
  - `payments`: one row per later payment, with its date.
- **stock**: the stock snapshot (base unit), plus `lots` (lot, expiry, import price, qty) for the pharmacies' expiry questions (119–124).
- **deliveries**: delivery runs (115–118).
- Not decided: expenses (lãi ròng; one shop per split) and the public-test phone-shop instalments / trade-ins (~26 questions): build them or abstain.

## Steps for each file type (agreed 2026-11-13)
The same order that worked for `sales_lines`:
1. **Survey** the files of that type in the train shops (like `survey/sales_lines_columns.md`, made with `scripts/checks/dump_headers.py` + a report script) → draft the schema.
2. **Hand-written reference converters** for a few train shops.
3. **Query side**: a finder for the table, with the rulebook's measures inside it, checked against `answers.jsonl`.
4. **analyst_agent**: an explore_agent writes a plan for that type's schema, then write_code reads the plan and converts the shop's files into the file type.

Steps 2 and 3 settle the schema against real answers before any agent work.

Measures come in two kinds, which the finder must tell apart:
- **flow over a period**: import money, average import price, top supplier, new debt, debt collected. These reuse the existing `period` / `group_by` / `top_n` arguments.
- **balance at the end of a day X**: debt outstanding, number of debtors, debt age, stock at X, value of lots expiring in N days. This is new: an `as_of` day instead of a period, summing everything up to that day.

## Where the code goes
- The analyst_agent part for non-sales_lines file types goes in:
  - `src/datamate/analyst_agent/explore_non_sales_line/`: the explore_agent that writes the plan for a non-sales_lines schema.
  - `src/datamate/analyst_agent/write_code_non_sales_line/`: write_code that applies that plan and writes the file type.
- Not discussed yet: where the hand-written reference converters and the new finders go.

## Open questions
- How much of explore_agent v2 and write_code can be shared rather than copied: which steps could take a schema description (columns, kinds, row checks, status enum) and which are tied to `sales_lines`. Not checked yet.
- The exact schemas (after each survey).

## Next
Step 1 for imports: survey the import files of the 12 train shops that have them and draft the `imports` schema.
