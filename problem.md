# ZaloAI DataMate agent: handoff notes

## Where we are (read this first; updated 2026-11-13)
The system is **two agents** over one shared standard table, `sales_lines`:
- **query_agent** (`src/datamate/query_agent/`, built): answers one question by calling tools (`find_entity`, `information_finder`, `calculate`) on a shop's converted `sales_lines`. It never reads raw files. Train live run (50 questions): OK 40, BAD 0, ABSTAIN 10. Public test run 1 (1000 questions, hand-converted data): **0.769** on the website. Since then `gross_profit` was added. The user considers it ~80% done for sales_lines questions; its remaining to-dos are mostly converter columns (see "Public test run 1"). Later commits (another developer) adapted it to a 9B model: a router to task families with one task tool each, a verify node, code repairs of small-model slips, thinking switches. **Its handoff file is `query_agent.md` (project root)**, written 2026-11-13 to make it faster.
- **analyst_agent** (`src/datamate/analyst_agent/`, **in progress**): turns an unseen shop folder (README + raw xlsx / csv / sqlite) into `sales_lines.parquet` + `manifest.json`, once per shop, offline. Hand-written converters (`src/datamate/reference/`, 5 train + 14 public-test shops) are its ground truth. Scope: sales_lines only. Shape now: `explore_agent → write_code → run_and_check → manifest`.
  - **explore_agent** (built, v2): a **fixed LangGraph workflow** of small steps that writes a `SalesLinePlan`. Each step: code fetches exactly the data it needs, the LLM answers one small constrained question (enums), code checks the answer at once. Every plan field is filled except `extra_attributes` (postponed). **Its own handoff file is `explore_agent.md` (project root): read that to work on the analyst_agent;** a summary is in "analyst_agent: current design (v2)" below.
  - **write_code** (built, stages 1–8): a fixed **no-LLM** pipeline that applies the plan and writes `data_std_agent/<split>/<shop>/`. Only what it cannot convert will go to the LLM, as predefined functions whose parameters the LLM fills (not designed yet). Details in `explore_agent.md` ("write_code").
  - **run_and_check**: not built. **Benchmark** (`scripts/bench_analyst.py`): built.
  - The v1 explore_agent (one think ⇄ search loop filling the whole plan; sections "explore_agent loop" ff. below) was **deleted** on 2026-11-11: its plans passed `plan_check` but were wrong.

**Model constraint (competition rule, corrected 2026-11-10): all models used at inference (LLMs, embedding models, rerankers, classifiers) total at most 12B parameters** (public weights released before 26 Oct 2026; fine-tuning and quantization allowed; declared in the README). The LLM target is ~9B (not chosen yet; whole explore runs so far on qwen3.5-9b). Development runs on Qwen3.8-27B on the user's server (earlier: claude-sonnet-5-5). Design rule: keep each job as simple as possible (few choices, short outputs, deterministic code and fixed steps wherever they can do the work). Lesson from the 9B runs: **one question at a time, with a one-sentence reason before the answer**; a long or mixed prompt makes it miss things.

Code layout:
```
src/datamate/
├── query_agent/    agent, prompts, tools, task_tools, state, finder, query, calc, entities, periods
│                   graph `query_agent`, state `QueryState`, context `QueryContext`
├── analyst_agent/
│   ├── explore_agent/  shared: explore (tools as plain functions), readme (column lists from the README),
│   │                   plan_check (the plan checked in code), state (`SalesLinePlan`, `ExploreContext`,
│   │                   `ShopState`), nodes (fixed steps confirm_headers, prepare), utils
│   │   └── v2/         the workflow: agent (graph + partial graphs), state (`WorkflowState`), one folder per
│   │                   step (filter_rows, find_line_tables, column_mapping, fill_missing_schema,
│   │                   missing_columns, status_words, line_kinds, cleaning_inputs, conversions, write_plan)
│   └── write_code/     plan → sales_lines: tables, joins, pipeline (stages 1–4), normalize (5), rules (6),
│                       units (7), finish (8)
├── bench.py        compares an agent conversion with the reference conversion of the same shop
├── parse.py        shared cell parsers + status / channel / payment maps
├── schema.py       shared sales_lines schema: finalize, validate, manifest, write
├── grading.py      shared §9 grader (also used by zalo_main.py)
└── reference/      hand-written converters: the analyst_agent's ground truth
```
Runners and checks: `scripts/run_agent.py` (query_agent; loads `.env`, the user runs it); `scripts/explore/run_explore_v2.py` and the per-step runners `scripts/explore/run_*_v2.py` (explore_agent; those calling the model load `.env`, the user runs them; list in `explore_agent.md` "Runners"); `scripts/write_code/run_stages_1_4.py`, `run_stage_5.py` … `run_stage_8.py` (stage 8 writes `data_std_agent/`); `scripts/convert_reference.py` (hand-written converters → `data_std/`); `scripts/bench_analyst.py --split <split> --candidate data_std_agent` (agent conversions vs references); checks in `scripts/checks/` (no LLM): `check_periods.py`, `check_query.py`, `check_tools.py`, `check_bench.py`, `check_readme_headers.py`, `check_plan_check.py`, `check_v2_line_tables.py`, `check_anchors.py`, `check_filter.py`, `check_shared_columns.py`, `check_order_spread.py`; `try_explore.py` (call one explore tool by hand). `scripts/run_explore.py` and `check_explore_agent.py` went with v1.

How to read this file: it is the whole project's history (survey → schema → reference conversion → query_agent → public test run → analyst_agent). Where an early idea was superseded, it says so. Current to-dos: "Public test run 1" (query_agent) and `explore_agent.md` ("Next" under "The graph" and under "write_code") for the analyst_agent.

## The task
Build an LLM agent that answers Vietnamese business questions about small shops' sales data (ZaloAI "DataMate" challenge).

Each question is one line of `questions.jsonl`:
```json
{"id": "q_001883280e83", "business_id": "biz_128", "question": "tháng này tổng giá trị các phiếu nhập hàng là bao nhiu", "as_of_date": "2026-09-07"}
```
- `business_id` is the shop's folder `<split>/data/<business_id>/`. It holds xlsx/csv/sqlite files and a `README.md` describing that shop's files, columns, unit conversions and conventions. READMEs differ per shop, and lists may be split across files (`_1`, `_2`, `_2025`) or sheets.
- `as_of_date` is "today" for the question ("tháng này", "hôm nay", ... are relative to it).
- `<split>/rulebook.md` defines every term (what counts as revenue, cancelled orders, periods, ties, rounding, unanswerable) and the **exact answer format** (§9). Read it before changing prompts; the answers depend on it.
- `<split>/calendar.csv` is a lunar calendar for Tết questions.

Dataset layout:
- `dataset/zaloAI/train/`: 3000 questions, `answers.jsonl` (answer key), 30 shops (`biz_101`–`biz_130`).
- `dataset/zaloAI/public_test/`: 1000 questions, no answers, 14 shops (`dev_001`–`dev_014`). Scored only on the competition website.

Answer key format: `{"id", "type", "answer", "alias"}`. Types and counts in train: number 1884, percent 578, list 193, date 145, string 88, unanswerable 70, boolean 23, table 19. `alias` holds alternative accepted values (usually product/customer codes): a list of strings for `string`, one list per element for `list`, one list per row (first column) for `table`.

Submission line: `{"id", "type", "answer"}`; unanswerable is `{"id", "type": "unanswerable", "reason": "..."}`. Scoring: +1 right, −1 wrong, 0 abstain (`{"type": "abstain"}`). The wrong format counts as wrong (e.g. money is an integer, percent is `12.4` not `0.124`).

Worked example (verified): q_001883280e83 in biz_128. "Phiếu nhập" are the rows of `DanhSachNhapHang.xlsx`, each row's value is "Cần trả NCC" (text with thousand separators, e.g. "13,353,000"), and dates are dd/mm/yyyy text. Summing 01/09–07/09/2026 gives 89,526,400, which matches the key.

## Earlier prototype (reference only)
`src/impls/zaloAI/` holds a code-writing agent (`agent ⇄ run_python → finalize`): correct answers but far too slow, because every question re-explores the raw files and writes pandas from scratch (a full train run ≈ a day, hundreds of millions of input tokens). It is not the base of the new design. Parts worth reusing:
- `zalo_main.py` (project root): runner with `--split`, `--ids`, `--limit`, `--out`; writes answers as it goes and scores against `answers.jsonl`. Its `is_correct` approximates the §9 grader (verified: all 3000 key answers score correct against themselves). It calls `load_dotenv()`, so ask before running it.
- Finalize step: `with_structured_output(Submission, method="json_schema")` with `type`, `answer_json` (a JSON string, parsed afterwards), `reason`. `method="json_schema"` is required: the default forced tool calling is unsupported on claude-sonnet-5-5.
- Tool arguments the LLM must not see (e.g. the shop folder) come from the state through `InjectedState`.
- Subprocesses must force UTF-8 stdout: the Windows console is cp1252 and crashes on Vietnamese.

## Design (agreed; progress in "Plan" and "Reference conversion" below)
Do the data exploration once, offline, so the agent never has to.

**1. One shared schema for all shops.**
- Define a fixed set of standard tables (file types) that every shop maps onto, e.g. sales detail lines, orders, products, customers, imports (supplier receipts). sales_lines is defined (below); the other file types are not yet.
- Example: biz_124's `BanHang_ChiTietHoaDon.xlsx` and biz_105's equivalent sales file both become the same standard table, with the same columns.
- A per-shop **conversion step** (LLM-driven, run once per shop) reads the README and raw files and writes the standard tables. It handles file names, split files (`_1`, `_2`, `_2025`), sheets, numbers stored as text with thousand separators, dd/mm/yyyy dates and unit conversions.
- It must work on unseen shops: public_test has `dev_001`–`dev_014`, which are not in train. Use the 30 train shops to check it. (This conversion step is now the **analyst_agent**; see its plan below.)
- Conversion must also normalize values: e.g. order status → a standard set (completed / cancelled / returned) whatever wording the shop uses ("Đã hủy", "Hủy", ...). Product attributes the questions filter on (e.g. active ingredient "hoạt chất", category) must be reachable from sales lines, by pre-joining or a lookup.
- Missing data (e.g. a shop has no status column) must be representable, so tools can report "not available" → `unanswerable`.

**2. Retrieval tools written once against the shared schema.**
- Several retrieval tools, each for a specific usage (e.g. one per standard table), with flexible parameters: filters (keyword + which column it targets, status, date / month / year / range), output column, aggregation (sum / count / count_distinct / ...), optional group_by, sort / top_n. (Superseded for sales_lines by the **measure-based** `information_finder`: the agent names a measure like `revenue` or `cancel_rate` and the tool applies the rulebook's row selection; see "Retrieval tools and answering agent".)
- Tools aggregate and return small results (a number or a short table), never raw row dumps.
- Rulebook semantics live inside the tools as parameters and defaults, so the LLM cannot forget them: what counts as revenue, what counts as cancelled, period boundaries relative to `as_of_date`, Tết via `calendar.csv`. Ties and rounding are handled in tools or finalize.
- A calculation tool (safe expression evaluation, not Python) for combining results. Rounding happens at finalize per §9.

**3. The agent only chooses tools and fills parameters.** It never reads raw files or writes code. It can call the same tool several times with different parameters. Example: share of a product group = one finder call for the group's revenue, one for all revenue, one calculation (`share(r1, r2)`); cancellation rate is a single measure, `cancel_rate`. Independent calls go in one turn; `ToolNode` runs them in parallel. The prompt carries the short shared schema plus the tool docs instead of the shop README. Target: roughly 1–2 tool turns plus finalize per question.

**Why it should be faster:** no exploration phase, no code writing or debugging loops, smaller prompts, parallel tool calls.

**Decisions made during the survey:**
- Schema first, from the column survey of the raw files; then one conversion pass per shop that cleans and maps together (the header row must be found before columns can be mapped, forward fill and subtotal removal come before order-level values mean anything, and parsing `25k` → 25000 *is* writing into the schema).
- Several standard file types (sales lines, imports, debt, ...). Every file of one type has the **same** schema in every shop.
- The agent first picks the file type it wants to work on, then is given the tool set for that file type. (Not built yet: today only sales_lines exists, and a question needing another file type is answered `OUT OF SCOPE` → abstain.)
- Shops whose README lists no columns (train: biz_106, biz_109, biz_123, biz_124) were skipped by the hand conversion. Note: 106 and 109 use the same POS export layout as 108 (only the README omits the columns); 123 (sqlite) and 124 (mixed sources, shop-specific column names) are the hard ones. The analyst_agent explores the files themselves, so it should handle them too; they are its hardest test cases, not a separate agent.

**How the tools and file types grow (agreed 2026-10-28, after the public-test abstain analysis).** Read this before adding a measure, a filter, a tool or a file type. Not built yet; the to-do list is in "Public test run 1".
- **Where new information goes: a column or a new file type?** Ask whether the information describes something that already appears on a sales line, or whether it has its own dated rows with amounts.
  - **Lookup tables** (product catalog, customer list, staff list) describe a product, customer or staff member that every sales line already refers to by key. Join them **at conversion time** into `sales_lines` columns, as `product_group`, `base_unit` and `customer_group` already are. The agent never joins files when answering. Example: dev_013's brand and formula number / diaper size come from `QuanLy.xlsx [DanhMuc]`, a different file, but they are product attributes, so they become columns. dev_012's vehicle model (inside the `Xe` cell) is the same case.
  - **Event tables** (imports, debt payments, stock lots, deliveries, instalments, expenses) have their own rows with their own dates and amounts, and they don't map onto sales lines. These are the real "other file types": each needs its own schema and its own tool, or the same tool over its own table. The agent first picks the file type (decision above).
  - Edge case: a lookup table queried on its own, e.g. dev_013 "new members registered in May" (`KhachHang`, "Ngày đăng ký"), which counts customers who may never have bought anything. This is a customer table in its own right, so it belongs to the file-type phase.
- **Don't split `information_finder` by rulebook section either** (proposed and discussed 2026-11-01). The rulebook's sections are §1 Đơn hàng và doanh thu, §2 Thời gian, §3 Xếp hạng, §4 Công nợ, §5 Kho, nhập hàng, giá vốn. Only some of them are data sources:
  - §2 (periods) and §3 (rankings) are rules that apply to every lookup, not data. They stay **arguments** shared by every finder (`period`, `group_by`, `top_n`, `compare`; shared code in `periods.py`). As tools, a question like "top 3 customers by revenue last month" (§1 + §2 + §3) would need three calls and a way to pass rows between them, and `calculate` only sees aggregated results.
  - §1, §4 and the stock / import part of §5 do match data sources, so the agreed split by data source lines up with them: `sales_finder` (today's `information_finder`, §1), `debt_finder` (§4), stock / import finder(s) (§5).
  - Lãi gộp is filed under §5 but is computed from sales lines (`qty × cost_price` per line, revenue's row selection), so it stays in the sales finder. Rule: a tool is defined by the table it reads.
  - Where the rulebook sections help: each tool's docstring and the system prompt say which sections the tool implements (e.g. "debt_finder implements §4: nợ thêm counts only ghi nợ orders not cancelled…"), so the agent can pick the right tool.
  - Agreed order for the query side: `gross_profit` first (done), then debt (§4) as the first event-table file type (biggest abstain bucket, ~48 public-test questions). That work now waits behind the analyst_agent.
- **Don't split `information_finder` by measure family** (e.g. a `money_information_finder` for revenue / unit price / gross profit, and a separate one for counts). The worry is right: the arguments do grow. But the growing part is filters and group_by (`size`, `shift`, `base_unit`, then brand, size, vehicle model, hour), not measures. A new measure is one more enum value plus one docstring line (e.g. `gross_profit`). Measure-family tools would each need the same period / filters / group_by / compare arguments, so the growing part would be duplicated, not shrunk. They also add a tool choice the agent can get wrong, and a cross-family question (a share of money vs quantity, a comparison) would need calls to two tools.
- **Split instead along two axes:**
  1. **By data source**, when the event-table file types arrive (above).
  2. **Generic product attributes instead of one filter field per attribute.**
     - The converter writes whatever extra product attributes the shop has as columns, and the manifest lists them.
     - The shop brief shows which attributes exist and their values, the way it lists product groups today.
     - The tool takes one generic filter (`attributes: {"size": "L"}`) and one generic grouping (`group_by: "attr:brand"`).
     - A new odd attribute in a future shop then needs no change to the tool schema.
- **A per-line quantity is a measure, not a `calculate` job.** `calculate` only sees aggregated results (r1, r2, ...), never lines. Gross profit = revenue − Σ qty × the cost price on each line, so it has to be computed per line before summing. A ranking ("the product with the most gross profit") would also need per-key subtraction of two grouped results, which `calculate` cannot do. So `gross_profit` is a measure, like `avg_order_value`.
- **Don't push filtering onto product names when the rulebook names a source column.** Example: dev_013 sizes come only from the catalog's `Số / Size` column, a blank cell means no size even if the name has one, and the brand is not always in the name (Enfagrow → Enfamil). So a "name contains" filter would give wrong answers; carry the catalog column. `find_entity` is different again: it resolves one fuzzy mention to one entity key; it does not select a set of products.
- **The brief must not call data OUT OF SCOPE just because the converter didn't map it.** If the raw files have it, map it, or note it as a to-do. Wrong OUT OF SCOPE notes cost about 20 public-test questions (dev_013 brand / size / vouchers; "giá vốn, lãi" in the system prompt's scope list while dev_003 / dev_006 record cost).

## Survey of the 30 train shops (done, read-only)
Scripts (both take the data folder, optionally followed by shop ids, e.g. `.venv/Scripts/python.exe -I scripts/survey_values.py dataset/zaloAI/train/data biz_105`):
- `scripts/survey_peek.py`: per file/sheet (or sqlite table), the sheet names and the first rows as raw cells. About 1 minute for all 30 shops.
- `scripts/survey_values.py`: reads every row; finds the header row, merges month sheets that share a header, then lists each column with ≤30 distinct values (statuses, units, channels...) and the format mix of the others (e.g. `s:9.9đ×76258, s:9k×22392`). Several minutes for all shops; its full output for the 30 train shops is saved in `survey_values_all.txt` (project root).

### File types found
| File type | Row = | Shops |
|---|---|---|
| sales_lines | one product line of an order | 29/30 (all except 110) |
| orders | one order: total after order discount, paid, debt, channel, payment method, seller, created time | separate file in 106, 108, 109, 110, 111, 119, 120, 123, 124, 127, 128, 129; elsewhere order fields repeat on every sales line |
| products | code, name, group, base unit, price, cost | 106, 108, 109, 111, 112, 115–124, 127–130 |
| unit_conversions | product, big unit, factor | file (102–106, 109, 117, 121–124) or list inside the README (101, 107, 108, 111–116, 119, 120, 125–130) |
| customers | code, name, phone, group | 106, 108, 109, 111, 119, 120, 123, 124, 127, 128, 129 (no group column: 106, 119, 120, 127) |
| payments | one payment against an order | 106, 109, 111, 123, 124, 127, 129 |
| imports | supplier receipt header + lines | 105, 106, 108, 109, 117, 119–124, 128 |
| stock | stock on the last data day (base unit) | 105, 106, 108, 109, 117, 119, 120, 124, 128 (123: stock ledger `the_kho`) |
| price_history | price changes | 105, 117, 123, 124 |
| lots | lot, expiry, qty sold / returned per lot | 119–124 (pharmacies) |
| drugs | active ingredient, prescription flag | 119–124 |
| deliveries | delivery runs | 115–118 |
| debt_terms | due date per order | 115–118 |
| one-offs | expenses (124), tables/areas (128), returns + staff (123) | |

Shops with gaps:
- 110: orders only, no line items.
- 107, 113, 126: no order code and no customer (one row = one product sold).
- 121, 122: import receipts without any money (quantities and lots only), so import-money questions are unanswerable there.
- Examples of `unanswerable` in the answer key: a month before the shop's first data day (103, 03/2025), stock questions in a shop without a stock file (118), customer-group questions in a shop without a group column (106), gross profit without cost data (122).

### Cleaning the converter must do
- **Header offset**: title rows (106 detail files, 112, 118, 124 `SoTay_`, 130), a filter row `Trạng thái đơn hàng | Đặt hàng (Đã duyệt)` (103, 121, 122), two-level headers (105, 117). Month-per-sheet files share one header across sheets.
- **Forward fill** (rulebook §8): in 103, 121, 122 about half the rows have order code / date / status / customer only on the order's first line.
- **Subtotal rows inside the data**: biz_104 has 443 `Tổng ngày dd/mm` rows, biz_110 has 429 `Cộng ngày dd/mm/yyyy` rows, biz_107 and biz_130 have `Tổng ngày dd/mm` in the product-name column (107: its 526 rows with an empty date; 130: its ~600 blank-key rows); 105 ends each sheet with `Cộng` + a note row, 117 with `Tổng tháng` + a note row. Keeping them double-counts revenue.
- **Duplicate rows within one order** are export errors and count once (§8).
- **Dates, mixed inside one column**: `dd/mm/yyyy`, Excel serial ints (`45689`), `1 thg 6 2025`, `1/1` without year (117: year of the sheet, §8), ISO, datetime objects.
- **Money**: `13,353,000`, `55.000đ`, `25k`, `25000k`, `1tr440`, `16,56tr`, `9tr960`, `25,000.0000`, `1.016.000đ`, negatives `-53.500đ`.
- **Quantity with unit inside**: `3 chai`, `1 ly`, `2 xe 4m3`.
- **Walk-in customer aliases**: `Khách lẻ`, `khách lẻ`, `kl`, `lẻ`. Customer lists append phone numbers to names (`Tên - 0912 365 003`, `Tên (0933…)`).
- **Name spellings**: stock files use other casing (`PEPSI 330ML`, `BẠC XỈU (M)`); same product (§8), join on code or normalized name.
- **Returns**: a return status (most shops), negative-quantity lines in the same order (105, 117), returned qty in the lot table (119–124).
- **`Phí phục vụ` lines** (125–130): counted in revenue, not a product (§3).

### Order status → the 4 standard statuses (§8)
- completed: Xong, xong, Hoàn thành, Đã hoàn thành, đã lấy, đã xong, Thành công, ok, Đã giao, completed
- cancelled: Đã hủy, hủy, Hủy, Huỷ đơn, hủy đơn, Đã hủy đơn, khách hủy, Không giao được, cancel, cancelled
- returned: Đổi trả, Trả hàng, trả lại, Hoàn trả, hàng trả về, Đã hoàn hàng, Đã trả hàng, returned
- in delivery: Đang giao, Đang giao hàng, Đang giao dịch, Chờ giao, Đang xử lý, đang gửi, Đang vận chuyển, shipping
- **Verified against the answer key** (`scripts/checks/status_month.py`: distinct orders per raw status per month):
  - `Không giao được` = cancelled. biz_116: replaces `khách hủy` from 04/2026; 4/2026 26 orders, 26/1190 = 2.18%, both match the key. biz_118: 3/2026 20/1105 = 1.81%, 7/2026 20/1042 = 1.92%. biz_124 (its only cancellation status): 1/2026 30/3237 = 0.93%. biz_107: key monthly revenue = sum of `Đã hoàn thành` only, so these rows are excluded.
  - `Đang xử lý`, `Đang giao dịch` = in delivery (counted in the rate's denominator, not as cancelled). They appear only in the last days of data. biz_118 9/2026: 7/344 = 2.03% (key 2.03; as cancelled it would be 4.07). biz_102 6/2026: 30/906 = 3.31% (key 3.31; as cancelled 4.19).
  - Seen in: `Không giao được` 107, 116, 118, 124; `Đang giao dịch` 102, 103, 104, 106; `Đang xử lý` 109, 118.
  - Side note for the converter: biz_107 has 526 rows with an empty date; the key's monthly revenue matches with them dropped.

Other small value sets: channel store / zalo_oa / phone; payment method cash / transfer / ewallet / cod / debt (Vietnamese in some shops: Tiền mặt, Chuyển khoản, Ví điện tử, Thu hộ (COD), Ghi nợ); customer group lẻ / thân thiết / sỉ.

### Question mix (rough keyword buckets over 3000 train questions)
About two thirds aggregate sales lines or orders: revenue, order count, cancellations, quantity in a unit, top-N products/customers, product group share, shifts, prices on a day. The long tail: stock, imports, debt and payments, lots/expiry, prescription/ingredient, delivery, gross profit.

### Proposals for the schema
- Always produce an **orders** file, derived from sales lines when the shop has no order file, so order-level questions (count, cancellation rate, average order value, top customer) always have the same home.
- One **manifest** per shop: which file types exist and which columns are empty. The agent reads it before picking a file type, so missing data leads to `unanswerable`, not a wrong 0.

## sales_lines: columns found and schema (draft)

### What the files contain
Full per-shop listing (raw column, README meaning, sample values): `survey/sales_lines_columns.md`. 29 shops have a sales_lines file (110 has none). Across all of them the columns reduce to **15 meanings**, with no shop-specific extras (the two "note" columns, `Ghi chú TT` in 113 and 117, actually hold the order status):

| Meaning | Present in |
|---|---|
| order id | all except 107, 113, 126 |
| product name | all except 111, 123, 127, 129 (code only; name comes from the products file) |
| quantity as written (`3 chai`, `2`) | all |
| line amount after line discount | all |
| unit, unit price | all |
| base-unit quantity | most; the others give a conversion list in the README or a conversion file |
| order date, status, customer | on the line when the shop has no orders file; in the orders file for 106, 108, 109, 111, 119, 120, 123, 124, 127, 128, 129 |
| product code | 106, 108, 109, 111, 119, 120, 123, 124, 127, 128, 129 |
| line discount | 106, 108, 109, 119, 120, 123, 124, 128, 129 |
| line number in the order | 106, 108, 109, 111, 119, 123, 124, 127, 128, 129 |
| order time | 105, 117 (on return lines: the time of the return) |
| cost at sale | 106, 109, 123, 124 |

Conclusion: for sales_lines a schema covering every column is small, so no extras mechanism is needed here. Free-text notes exist in other file types (`Ghi chú` / `Diễn giải` in the orders files of 106, 120, 123, 127; import files), so the extras question comes back when we design those.

107, 113, 126 have no order or customer questions in the train key (only "price on day D"). Their `order_id` stays null, each row counts on its own (§8 "Bảng không ghi theo đơn"), and order/customer questions there are `unanswerable` (§7: do not infer what the file does not record).

### Schema
One row = one product line (or service-fee line) of an order, after cleaning: header found, forward fill, subtotal rows removed, duplicate lines within one order counted once (§8). Every column may be null; the shop manifest lists which columns the shop actually has.

Order-level columns (date, time, status, customer) are **copied onto every line** from the order, also in shops that have a separate orders file. Product attributes used as filters (group, base unit) are copied from the product. So a sales_lines tool never needs a join. Channel, payment method and seller are also copied onto every line (decided later, see the columns below). The orders table stays the home of the remaining order-only fields (order discount, paid, debt).

| Column | Type | Meaning / rule |
|---|---|---|
| `order_id` | str | order code; null in 107, 113, 126 |
| `line_no` | int | line number in the order, when the shop records it |
| `order_date` | date | the day the order was placed (§2: periods go by order date). For return lines in 105/117, taken from the order's sale lines |
| `order_time` | datetime | time the order was placed, when recorded (105, 117, orders files with a created time) |
| `status` | enum | `completed` / `cancelled` / `returned` / `in_delivery` (mapping in "Order status" above). Order-level: an order with any return line is `returned` as a whole (§1) |
| `customer_key` | str | identifies the customer (§3): customer code if the shop has codes, otherwise the normalized name (lower case, single spaces, phone suffix removed; accents kept). Null for walk-in (`Khách lẻ`, `kl`, `lẻ`) and when not recorded |
| `customer_name` | str | customer name as written, phone suffix removed |
| `customer_group` | str | `lẻ` / `thân thiết` / `sỉ` from the customers file; null when the shop has no group column |
| `channel` | enum | `store` / `zalo_oa` / `phone` (order-level, copied from the orders file; `parse.map_channel`) |
| `payment_method` | enum | `cash` / `transfer` / `ewallet` / `cod` / `debt`, as written on the order (§1; `parse.map_payment`) |
| `seller` | str | staff code as written (e.g. `NV003`); §3 best seller = by revenue |
| `product_key` | str | identifies the product (§8): product code if the shop has codes, otherwise the normalized name |
| `product_code` | str | product code as written |
| `product_name` | str | product name; from the products file when the line has only a code |
| `product_group` | str | product group from the products file |
| `line_kind` | enum | `product` / `service_fee` (`Phí phục vụ` lines, 125–130: in revenue, not a product, §3) |
| `is_return_line` | bool | line that records goods coming back (negative quantity in 105, 117) |
| `unit` | str | unit written on the line, normalized (lower case, trimmed) |
| `qty` | float | quantity in `unit`; negative on return lines |
| `base_unit` | str | the product's base unit |
| `unit_factor` | float | base units per one `unit` (1 when `unit` is the base unit; from the conversion file or README list) |
| `qty_base` | float | `qty × unit_factor`; when the file records it, the converter checks it against the factor |
| `return_date` | date | on return lines only: the date written on the line, which is the date of the return (105, 117) |
| `unit_price` | int | price per one `unit`, before line discount (VND) |
| `line_discount` | int | discount on the whole line (not per unit), VND. Shops without a discount column: `qty × unit_price − line_amount` (≥ 0 on sale lines, negative on return lines because qty and amount are negative there) |
| `line_amount` | int | line amount after line discount (VND); the unit of revenue (§1) |
| `cost_price` | int | cost per one `unit` recorded on the sales line (106, 109, 123, 124); needed for gross profit (§5), otherwise `unanswerable` |
| `source_file`, `source_sheet`, `source_row` | str, str, int | where the line came from, for debugging |

Derived in tools, not stored: price per base unit = `unit_price / unit_factor` (§5 "Giá bán X ngày D"); order revenue = sum of `line_amount` of the order (§1).

### Checks done on the raw files (all 29 shops)
- **Line discount is per line, not per unit.** In the 9 shops with a discount column, every line (~870k) fits `line_amount = qty × unit_price − line_discount`; on lines with qty > 1 only this formula fits.
- **`unit_price` is always before discount.** In shops without a discount column, 97–99% of lines fit `qty × unit_price` exactly; the rest are a little lower (mostly ×0.95), an unrecorded line discount. Never higher, except return lines (negative amounts).
- **Return lines (105, 117):** every order with a negative-quantity line also has its sale lines (105: 350 orders, 117: 114), and all lines of such an order already carry a return status. Partial returns exist (sold 2, returned 1). The date/time on a return line is the return, always later than the sale, so `order_date` must come from the sale lines (otherwise the return falls into another period).
- **Unparsed rows are all subtotals or footers:** `Tổng ngày dd/mm` (104 in the customer column; 107 and 130 in the product column, which explains 130's ~600 blank-key rows), `Cộng` + `Ghi chú: tiền tính bằng đồng` at the end of each sheet (105), `Tổng tháng` + `Ghi chú: số liệu ghi tay` (117).
- **Storage: parquet.** `pyarrow` is installed (the project uses uv: `pyproject.toml`, `uv.lock`; the venv has no pip). Parquet keeps types (dates, ints, nulls) and loads fast into pandas.

Scripts in `scripts/checks/` (read-only, one-off; the first argument of `check_*.py` is the folder holding `lines_lib.py`, i.e. `scripts/checks`): `lines_lib.py` (raw loader + early parsers, superseded by `src/datamate/parse.py`), `check_amounts.py`, `check_returns.py`, `status_month.py`, `dump_headers.py` (headers of every file → JSON) and `sales_lines_report.py` (JSON → `survey/sales_lines_columns.md`; the same pair can produce the listing for other file types).

## Plan (agreed): sales_lines end to end first
1. Shared sales_lines schema (above). Done.
2. **Reference conversion** of 5 train shops by hand-written code, checked against the key. Done (below); later also all 14 public-test shops. It lets tools and the answering agent be built and scored before the conversion agent exists, and is the reference the conversion agent's output is compared to.
3. Retrieval tools on sales_lines. Done (see "Retrieval tools" below).
4. Answering agent (**query_agent**) for sales_lines questions. Done and run live (train 40/0/10 of 50; public test 0.769). A question outside sales_lines **abstains** (0), never `unanswerable` (the shop may have that data: −1). Inside sales_lines, answerability is decided after the tool calls (missing column in the manifest, period outside the data → `unanswerable`).
5. Conversion agent (**analyst_agent**): builds the sales_lines table from a shop's files (line file + orders / products / customers / conversion files), with self-checks for shops without a key. **In progress:** explore_agent (v2) and write_code built, run_and_check not; see "analyst_agent: current design (v2)" and `explore_agent.md`.
6. Later: event-table file types (debt first, then stock / imports, ...), each with its own schema, an analyst_agent conversion and a query_agent finder.

## Reference conversion (done)
Code: `src/datamate/` (`parse.py` cell parsers + status map, `schema.py` columns / finalize / validate / manifest, `reference/common.py` shared steps, `reference/biz_<id>.py` one converter per shop). Run: `.venv/Scripts/python.exe -I scripts/convert_reference.py [shop ...]` → `data_std/train/<shop>/sales_lines.parquet` + `manifest.json` (columns available / missing, date range, README data end, status counts).

| Shop | Lines | Orders | What it exercises | Notes from the run |
|---|---|---|---|---|
| biz_108 | 78,983 | 31,501 | separate orders, customers, products files joined by code | file's converted qty agrees with the README list on every line |
| biz_116 | 39,889 | 18,099 | name-only lines joined to the product sheet; statuses change in 04/2026 | 202 lines spell product names without accents (`dinh 5 phan`), matched accent-insensitively |
| biz_117 | 33,332 | 14,992 | month sheets, dates without year, `1tr440` money, return lines | 40 footer rows and 473 duplicate lines dropped; 114 returned orders = the 114 orders with negative lines |
| biz_122 | 103,663 | 55,079 | forward fill (48,584 lines), split files, conversion file | 491 returned orders = the 491 orders with returns in the lot file |
| biz_128 | 98,507 | 26,505 | service-fee lines, month-sheet orders, no unit column | 1,143 duplicate lines dropped; unit of 539 big-unit lines taken from the README list by factor |

**Checked against the key** (`scripts/check_sales_lines.py`, plain pandas on the parquet, no agent): every plain revenue (month / quarter / day / this month), cancelled-count and cancellation-rate question of the 5 shops matches: revenue 64/64, cancelled count 26/26, cancellation rate 29/29. By hand also: 117 "orders with revenue ≥ X" 6/6 (confirms the duplicate removal), 108 average revenue per order 5/5, 122 year-to-date revenue and "days with revenue ≥ X". The script's other misses are questions its regex misreads (per product, per ingredient, per order), not data errors.

Decisions made while converting:
- **Product names** match accent-insensitively when no exact match (`fold_accents`): §8 says spellings of one name are one product. **Customer names** do not: no shop has accent variants of a customer name (checked 116, 117, 122), and a different tone mark can be a different person. (The analyst_agent's write_code differs on purpose, agreed 2026-11-13: names differing only in accents share one `customer_key`, because biz_108's 57 accent pairs all share one customer code and dev_014 writes both spellings within one order; see `explore_agent.md`, write_code gap 3.)
- Walk-in aliases (`Khách lẻ`, `kl`, `lẻ`, ...) → `customer_key` null. Phone suffixes are stripped from names.
- An unknown status, money, quantity or date format raises an error instead of being guessed.
- 117's return lines: `order_date` / `order_time` from the order's sale lines, the return line's own date goes to `return_date`.

## Retrieval tools and answering agent (built; first live run: 40 OK / 0 BAD / 10 ABSTAIN of 50)
Agreed design: `find_entity` (fuzzy text → exact key), `information_finder` (measure-based: the measure fixes the rows per rulebook; period resolved in the tool; optional `compare="previous"`; stores the full sorted frame in the graph state as a handle `r1`, `r2`… and returns shape + description + top rows), `calculate` (safe expressions over scalars and handles, e.g. `count(setdiff(r1.product_key, r2.product_key))` for "mặt hàng ế").
- Measures (`query.MEASURES`): `revenue`, `order_count`, `avg_order_value`, `qty_base`, `customer_count` count completed + in_delivery orders; `cancelled_count`, `cancel_rate` and `unit_price_base` use all statuses; `qty_base` and `unit_price_base` use product lines only (no service fee). `group_by`: product, product_group, customer, customer_group, seller, channel, payment_method, order, day, week, month, shift. Period kinds (`periods.KINDS`): day, today, yesterday, this_week, last_week, this_month, month, quarter, year, year_to_date, last_n_days, range, all_before, tet. A month without a year = the latest one not after as_of.
- User decisions on the design: `find_entity` is called **only** when the question names an entity. The prompt's "whenever the question names a product, ..." was enough: in the first live run (50 questions) all 11 calls were on named entities, so no stricter wording was added. There is **no** special-purpose tool for things like unsold products ("mặt hàng ế"): the agent combines finder handles in `calculate` (e.g. `count(setdiff(r1.product_key, r2.product_key))`). New question patterns should be solved the same way (prompt guidance + calc functions) before adding a tool. The user is learning LangGraph through this project, so explain changes rather than just making them.
- Step 1 done: `src/datamate/query_agent/periods.py` (`resolve_period`, `previous_period`, `LunarCalendar`), checked by `scripts/checks/check_periods.py` (hand-worked cases). Assumptions to verify against the key: windows cut at as_of; this_week compared with the same weekdays of last week; year_to_date with the same span last year.
- Step 2 done: `channel`, `payment_method`, `seller` on sales_lines. Of the 5 reference shops only 108 has them (channel + seller; none has payment method). Checked: 108 best seller 7/2025 = NV003, Zalo OA share 12/2025 = 4.23, store revenue 1/2026 = 284,581,200, all match the key.
- Step 3 done: `src/datamate/query_agent/query.py`. `Shop.load` (parquet + manifest), `run_query(shop, measure, start, end, filters, group_by)` → sorted frame `[key, code, name, value]` (or `[value]`), `compare(cur, prev, measure)` → adds `value_prev`, `diff`, `pct_change`. Raises `NoData` (→ unanswerable: period outside the data, column not recorded) and `QueryError` (bad parameters, message for the agent). Checked by `scripts/checks/check_query.py`: 54 hand-written call plans for sample questions of every intent (revenue by period, counts, cancel rate, avg order, compare, price-increase effect, shifts, product / group / channel / seller / customer-group filters and shares, top-N customers, best day, best seller, "≥ 3 orders", ế, monotonic trend): 54/54 match the key. This also confirms the period windows of step 1 on real answers (last_week, this_week, last_n_days, this_month vs same days, year_to_date, range compare).
- Fixed while doing step 3: in 108 and 128, walk-in orders had `customer_key = "nan"` (a blank code stringified), so "Khách lẻ" ranked as a customer. `validate` now flags any string column holding the text `nan` / `None`.
- Step 4 done: handles + calculate, still without LangGraph. `src/datamate/query_agent/finder.py` `information_finder(shop, as_of, lunar, handle, measure, period, filters, group_by, top_n, compare)` → `FinderOutput(text, handle, frame, no_data)`: the text is what the agent reads (period(s) resolved, rows counted, notes, the value or the top rows); the full frame is kept by the caller under the handle. `src/datamate/query_agent/calc.py` `calculate(expr, results)`: `ast` whitelist (arithmetic, comparisons, and/or/not, lists; no attribute access except `rN.<column>`); ungrouped handles read as numbers, grouped ones as tables; functions `count total mean first top value where setdiff intersect pct_change share increasing decreasing round min max abs`. `check_query.py` now runs each case as finder calls + one calculate expression, i.e. exactly the agent's plan: 54/54.
- Step 5 done: LangGraph tools. `src/datamate/query_agent/state.py` `QueryState` (question, as_of_date, shop_dir, calendar_path, messages, `results` with a merge reducer, submission). `src/datamate/query_agent/entities.py` `candidates(shop, kind, text)`: accent-insensitive word coverage / difflib similarity against the shop's own values (all 14 sample phrases hit the right value first). `src/datamate/query_agent/tools.py` `TOOLS = [find_entity, information_finder, calculate]`: Pydantic arg schemas (`Period`, `Filters`), injected args hidden from the LLM, `information_finder` returns `Command(update={"messages": [ToolMessage], "results": {handle: frame}})`. Handles are numbered by earlier finder ToolMessages + position among this turn's finder calls, so parallel calls never collide (a NO DATA / error call uses up its number). Checked by `scripts/checks/check_tools.py` (hand-written AIMessages through a real ToolNode, no LLM): all checks ok.
- Step 6 done (first live run below): the answering agent. `src/datamate/query_agent/agent.py` `query_agent` = agent ⇄ tools → finalize (same shape as the prototype). `src/datamate/query_agent/prompts.py`: SYSTEM (tool guide, question → calls mapping, scope rules, rulebook) is identical for every question and marked `cache_control` for prompt caching; USER carries the question, as_of_date + weekday and a shop brief from the manifest (data range, what is not recorded, product / customer groups). Safety nets in finalize: reply `OUT OF SCOPE` (question needs non-sales data) → abstain without an LLM call; `unanswerable` is kept only if a tool really returned NO DATA, else abstain; a malformed number → abstain. `group_by="order"` added (count orders with revenue ≥ X; 2 more cases in `check_query.py`, now 56/56). Grader moved to `src/datamate/grading.py` (zalo_main imports it). Runner: `scripts/run_agent.py` (loads .env): `--sample N`, `--shops`, `--ids`, `--limit`, `--workers`, `--model`; writes `runs/<timestamp>/submission.jsonl` + `traces.jsonl` (every tool call and output) and prints OK / BAD / ABSTAIN, score, per-type counts, time, tool turns, tokens.
- Implementation notes for step 6: the model and rulebook are passed at invoke time as a `QueryContext` (LangGraph `Runtime` context, `context_schema`), not stored in the state. finalize uses `with_structured_output(Submission, method="json_schema")`, because forced tool calling (the default method) is unsupported on claude-sonnet-5-5. `RECURSION_LIMIT = 14` (about 6 tool turns); a `GraphRecursionError` or any exception → abstain. Questions run in a thread pool (`--workers`, default 1). A float answer with an integer value is submitted as an int.
- Checked without an LLM: `check_query.py` 56/56, `check_tools.py` all ok, `check_periods.py` 24/24, the graph compiles (agent, tools, finalize), the shop brief renders, and the system prompt is about 19k characters.
- First live run (2026-10-28, `--sample 50`, `runs/20261028-145112/`): OK 40, BAD 0, ABSTAIN 10, score +40. The ABSTAINs: 3 × biz_122 drug attributes (prescription, active ingredient: drugs table), 2 × imports (128), 3 × stock (128, 117), and 3 whose key is `unanswerable` (customer street, customer gender, expiry in 117). find_entity was called in 11 questions, all on named entities, all OK.
- (Superseded by "Public test run 1" and the analyst_agent plan; the larger train run was not done.) Next: a larger run (all ~500 questions of the 5 shops) to find BAD answers in sales_lines questions; then the long tail. Options: pre-join drug attributes (prescription, active ingredient) onto sales_lines for 119–124 as product attributes (cheap, same tool); add more file types (imports, stock); and decide when "not recorded" may become `unanswerable` instead of abstain (3 points lost here, but −1 if wrong).

**Fixed (2026-10-28): blank → `"nan"` in the reference converters.** `parse.clean_text(v)` (None for blanks, stripped text otherwise) now replaces every `str(v).strip()` on a raw cell: catalog product names (108, 116, 122, 128), customer code (108, 128), seller (108), and `common.canonical_names` (a whitespace-only name used to become the product name `""`). It was latent: regenerated parquet + manifests are identical to before, `check_query.py` 56/56. The conversion agent should use `clean_text` too; `schema.validate` still flags `nan` / `None` text in any string column.

## Public test reference conversion (done 2026-10-28)
Goal: the ideal-data case for the agent on the 1000 public-test questions, scored on the competition website (no `answers.jsonl` for this split). Run: `.venv/Scripts/python.exe -I scripts/convert_reference.py --split public_test [shop ...]` → `data_std/public_test/<shop>/`; the agent: `scripts/run_agent.py --split public_test` (default shops follow the split; the user runs it, it loads `.env`).

- **The test rulebook differs from train** (`diff` the two files): adds industry rules for mẹ và bé, sửa xe, cửa hàng điện thoại, café toppings in the drink's cell, "Sổ không có cột trạng thái → Ghi chú", new rankings (khách trung thành / sộp / hay đổi ý, giờ cao điểm, thợ chăm chỉ ...), and drops "Giá bán X ngày D" and "Tồn theo đơn vị lớn". `run_agent.py` already loads the split's own rulebook.
- **Checks (no key):** `scripts/checks/check_subtotals.py --split public_test` re-reads the subtotal rows the exports contain and compares them with the converted lines: every one matches the sum of all lines of its day / month (dev_001 554, dev_002 526, dev_004 18, dev_007 616, dev_008 587, dev_009 540, dev_014 15). Order-level item totals match the converted lines of every order in dev_003, 006, 007, 008, 010 (198,039 orders). Not cross-checked by totals: dev_005 (statuses agree with the lot file's returned quantities), 011, 012, 013. Value surveys: `survey_peek_test.txt`, `survey_values_test.txt` (project root).
- **Shared code added:** `common.drop_repeated_headers`, `common.read_sqlite`, `Converted.brief` → `manifest["brief"]` → shown in the agent's shop brief (how an odd shop maps onto sales lines); `agent.FEATURES` gained `product_key` (shops without products say so); `schema.validate` skips required columns that are empty on every line (shop does not record them); `query.MEASURE_NEEDS` for `qty_base` / `unit_price_base`; channel `ecommerce` (Sàn TMĐT); status words `khách trả`, `trả lại hàng`, `khách mang trả`, `khách trả lại` (returned), `đang ship`, `chưa giao` (in delivery); subtotal regex `tổng d/m`, `tổng cộng`, `tong cong`, `cộng tháng`. Train output regenerated: identical, `check_query.py` 56/56.

| Shop | Kind | What the converter does |
|---|---|---|
| dev_001 | grocery POS | title block, names joined to the product list, README conversions |
| dev_002 | café | month files, repeated headers, no product list (`canonical_names`), toppings in the drink's cell split into `Topping <name>` lines (§6 Cà phê) |
| dev_003 | café POS | two yearly books, codes, cost at sale, customers + groups, payment method |
| dev_004 | building materials book | forward fill, year-less dates, returns as negative lines (like biz_117), `bó` = 10 cây from the README |
| dev_005 | pharmacy | like biz_122; nested conversions (`hộp = 15 vỉ`); returns cross-checked with `DonHang_Lo.xlsx` |
| dev_006 | pharmacy POS sqlite | `seller` = staff **name** (questions name staff; grader accepts name or code) |
| dev_007, dev_008 | restaurant | service-fee lines; 007 has no unit column (unit from the README factor) |
| dev_009 | order list only | one line per order, amount = `Giá trị hàng`; no products |
| dev_010 | mẹ và bé CSV | orders / lines / products / customers by code |
| dev_011, dev_014 | phone shop books | `phone_shop.py`: `Ghi tắt` abbreviations (accent-insensitive), group = máy mới / máy cũ / phụ kiện, status from Ghi chú; **assumed** unit `chiếc` (phones) / `cái` (accessories), the book has none |
| dev_012 | motorbike repair | one line per ticket, `seller` = mechanic full name, `customer_key` = licence plate; jobs not mapped |
| dev_013 | mẹ và bé books | drops Voucher / Dùng điểm / Phí ship rows (not revenue), status from Ghi chú, conversions parsed from DanhMuc |

Known gaps (the agent should abstain; see "Public test run 1" below: several of these are not real gaps): drug attributes (prescription, active ingredient: dev_005, dev_006), imports, stock, debt / payments / instalments, trade-ins, warranty, delivery runs, expenses, dev_012 jobs / models / commission, dev_013 vouchers / brands / sizes / members, customer address. dev_012's average per ticket should exclude warranty tickets (amount 0); `avg_order_value` does not.

## Public test run 1 (2026-10-28): score 0.769, abstain analysis, query_agent to-dos (1 of 6 done; the rest paused for the analyst_agent)
Full run `runs/20261028-192030` (1000 questions), submitted on the website: **0.769**. Submissions: number 455, abstain 217, percent 176, list 51, string 45, date 37, boolean 11, unanswerable 8.

**Trace recording added to `scripts/run_agent.py`** (for diagnosing slow / failed questions): `query_agent.stream(..., stream_mode=["updates", "values"])` instead of `invoke`, so the messages reached so far survive a `GraphRecursionError` (before, a recursion-limit question had an empty trace). Each `traces.jsonl` record now has `started` (seconds after the run began), `seconds`, `tool_turns`, `usage` (input / cache_read / cache_write / output; langchain-anthropic reports cache writes under `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`), `steps` (seconds per graph node) and `usage` per AI message. The finalize call's tokens are not recorded (its reply is not in the messages), only its time.
- Slow questions in the first 20-question sample were API latency, not the test set: rerun with the same seed took about half the time for the same tool turns. A model call takes 1.3–4.6 s; a 1-turn question makes 3 calls (tool call, reply, finalize ≈ 2 s). The test rulebook (23.8 KB vs 18.5 KB) is cached and costs little.
- Multi-step questions are non-deterministic: `q_dd2362109dd4` (dev_003, "since Bạc xỉu (M) went up to 35.000đ, first 14 days vs the 14 before") hit the recursion limit once and was answered correctly (−9.24, checked by hand) on the rerun.

**Abstain analysis** (scratchpad scripts `abstains.py`, `abstain_topics.py`): 214 of 217 abstains are the agent declaring OUT OF SCOPE on purpose (211 before any tool call), as the system prompt's scope list and the shop briefs tell it; only 3 are dead ends:
- dev_012 "xe vô sửa từ 2 lần trở lên" (recursion limit; answered 44 in another run, probably wrong: the "xe khách quen" rule counts only paid tickets and the tools cannot exclude warranty tickets with amount 0);
- dev_004 giờ cao điểm (tools group by shift, not by hour; rulebook: the one-hour slot with most orders, answer = start hour);
- dev_008 "Canh chua cá tràu" (not a product of the shop; abstain is fine).

Abstains by topic (keyword buckets, approximate): công nợ / thu nợ / bán chịu 48, lãi gộp / lãi ròng ~20, tồn kho / lô / hạn dùng 18, thuốc kê đơn / hoạt chất 17, nhập hàng / NCC 15, dev_013 size / hãng / số sữa 12, phone-shop trade-ins / khách phải trả / trả góp / bảo hành ~26, dev_013 voucher / ship / điểm / thành viên 9, dev_004 đợt giao 8, dev_012 dòng xe 6, dev_012 hoa hồng / việc sửa ~8, khách giới tính / địa chỉ 7, subjective 6.

**Cheap wins without new file types (~45 questions)**: these abstains come from our own rules, not missing data:
1. **Done 2026-11-01: `gross_profit` measure** (`query.py`; tool enum + docstring, prompt mapping, scope list now says "giá trị tồn kho, lãi ròng" instead of "giá vốn, lãi", `agent.FEATURES` lists `cost_price`). Service-fee lines cost 0; a product line without cost → NoData. Checked: the 7 public-test lãi gộp / lời nhất questions of dev_003 / dev_006 match plain pandas (scratchpad `gp_check.py`); dev_014 → NO DATA (no cost recorded); `check_query.py` 56/56, `check_tools.py` ok. Not yet run live. Original note: **Gross profit** (~13, dev_003 and dev_006): rulebook §5 lãi gộp = revenue − Σ qty × the cost price written on the sale line (per sale unit); `cost_price` is filled on every line of both shops, but the system prompt lists "giá vốn, lãi" as OUT OF SCOPE. Add a `gross_profit` **measure** to `information_finder`: it cannot live in `calculate`, which only sees aggregated results (cost must be multiplied per line before summing, and "product with the most gross profit" would need per-key subtraction of two grouped results, which calculate cannot do). Drop "giá vốn, lãi" from the scope list for shops that record cost. Lãi ròng still needs the expense book.
2. **dev_013 brand / size / formula number** (12): the brief I wrote marks them OUT OF SCOPE; that was a shortcut and wrong. `QuanLy.xlsx [DanhMuc]` has `Hãng` and `Số / Size`. Rulebook (Mẹ và bé): number / size come from that column only (a blank cell = no size even if the name has one; brand is not always in the name: Enfagrow → Enfamil), shares are over all **bỉm tã** bịch of the period. So a "name contains" filter would be wrong; carry the catalog columns into `sales_lines`.
3. **dev_013 vouchers, points, shipping** (9): keep the rows the converter now drops as non-revenue lines (like `service_fee`); rulebook: positive amounts, by invoice date, invoices not cancelled / returned only.
4. **dev_012 vehicle model** (6): it is in the `Xe` cell (`customer_name`, e.g. "Vario 125 59M5-357.11"); extract it into a column. Also add a paid-tickets-only option (fixes the dead end above and `avg_order_value` including warranty tickets).
5. **Subjective questions** (6: "ngon nhất", "trung thực nhất", "thân thiện nhất", "nhà cung cấp uy tín"...): rulebook §7 → `unanswerable`, but our rule allows unanswerable only after a tool's NO DATA, so they score 0 instead of a likely +1.
6. **Peak hour**: add group by hour.

Status (2026-11-01): 1 is done. 2–4 are **converter work** (columns the converter must produce: generic product attributes, non-revenue line kinds, vehicle model, a paid-ticket flag), so they are folded into the analyst_agent: its output must carry them. (Suggestion, not yet agreed: add them to the hand-written references 012 / 013 first, so the bench can check them.) 5 (subjective → unanswerable) and 6 (group by hour) are small query_agent changes, left for later.

**Design decisions:** see "How the tools and file types grow" in the Design section (split by data source and generic product attributes, not by measure family; lookup tables become columns, event tables become file types).
- The remaining ~170 abstains (debt, stock / lots, imports, drug catalog attributes, phone-shop trade-ins / instalments / warranty, deliveries, commission, expenses) need the other file types: the larger prize (~17% of the test set).

## analyst_agent: plan (agreed 2026-11-01) and progress

### Goal and scope
- Input: one shop folder `<split>/data/<shop>/` (README.md + xlsx / csv / sqlite). Output: `sales_lines.parquet` + `manifest.json` in the same format as `scripts/convert_reference.py` writes (`schema.write`), so the query_agent runs on it unchanged.
- It runs **once per shop, offline**. Minutes per shop and a few retries are fine; that is the opposite of the old prototype, which wrote code for every question.
- Scope: **sales_lines only** (agreed). Event tables (debt, stock, imports, ...) come later, built the same way: one schema + one conversion per file type.
- Why it matters: the hand-written converters only cover the 19 known shops. The final test will have unseen shops, and the query_agent is useless there without a converter.

### analyst_agent: current design (v2, 2026-11-10 → 2026-11-13; details in `explore_agent.md`)
Everything from "Approach" down to "Open questions" below is the **v1 history** (2026-11-01 → 02). Where it disagrees with this summary, this summary wins.
- **A fixed workflow, not an agent loop** (user: "much safer and we can directly control the input/output"). One LangGraph node per step; each step has its own check and retry, so a mistake in step 2 never sends step 1 back. Temperature 0. No LLM fills `SalesLinePlan` any more: steps fill its fields part by part, and the last step puts it together in code. Agent mode only as a fallback for a step too flexible for a workflow (odd structures).
- **Structured replies:** per-shop `pydantic.create_model` with `Literal` enums (an invented column name fails validation), `method="json_schema"`, small `max_tokens`; no optional-length lists or free text except one `reason` field written before the answer.
- **No hard-coded values from the 44 known shops** (unseen shops write things differently). Exception: step 5's known status-word list (`parse.STATUS_MAP`), because some words are dataset conventions no model can work out (`Đang giao dịch` → in_delivery, `Không giao được` → cancelled). `SUBTOTAL` and `map_status` stay as answer keys.
- The explore_agent's graph:
  ```
  START → confirm_headers → prepare → filter_rows → choose_line_tables → map_line_columns → find_anchors → attach
        → resolve_anchors → snapshot → report_missing → map_statuses → map_line_kinds → cleaning_inputs
        → map_conversions → write_plan → END
  ```
  - step 0 `filter_rows`: repeated headers by code; subtotal / note rows found by shape (blank in a dense column), grouped into patterns, the LLM judges each pattern; rows are marked (`skipped_rows`), not deleted; per-shop `subtotal_labels` regexes replace the global `SUBTOTAL`.
  - step 1 `choose_line_tables`: one enum over the table groups.
  - step 2 `map_line_columns`: a subgraph (answer ⇄ check → llm_check → shared_recheck → open_fields). One enum per schema column; code checks kinds, `line_amount = qty × unit_price − line_discount`, shared columns; the amount fields and `qty_base` are decided by code; `open_fields` asks each still-open field alone with a reason.
  - step 3 (fill from other tables): `find_anchors` (LLM claims anchor columns: order / product / customer code or name), `attach` (code proves them by values, in rounds; code + name pairs, alias pairs for `Ghi tắt`), `resolve_anchors` (picks the shop's own list when several tables attach), `snapshot` (asks each attached table only the fields its anchors can hold). The field resolve between conflicting tables is not built.
  - step 4 `report_missing` (code), step 5 `map_statuses` (hybrid known words + LLM words call + a separate blank-status call; 5e asks the blank question also when there is no status column), step 7 `map_line_kinds` (service-fee / not-revenue names: top-K candidates by code, each non-item asked again alone with a reason), step 6 `cleaning_inputs` (code: forward fill columns, return lines, duplicate count), `map_conversions` (README list by regex; else README lines asked one by one; else which table and its columns, format and key by code), step 8 `write_plan` (code; `plan_check` as the guard).
- **`SalesLinePlan` changes since v1:** `blank_status`, `skipped_rows`, `header_rows`, `Join.alt_key` (code + name pairs), `conversions` is now a list of `ConversionSource` (README list / table / README text), `subtotal_labels` are regexes.
- **write_code** (replaces "the LLM writes `convert(shop_dir)`"): a fixed no-LLM pipeline. Stages: (1) read each line table with the plan's header rows and skipped rows; (2) forward fill; (3) joins as row pointers; (4) select and rename; (5) one parser per value kind (year-less dates from the sheet name checked against the sheet's full dates); (6) rules: status per order + order-level spread (blanks only, conflicts counted), return lines, line kind, not-revenue rows dropped, duplicates dropped; (7) units and conversions → `unit_factor`, `qty_base`; (8) keys, implied discount, one `order_id` per row when the shop has none, `finalize` / `validate`, write. A cell a parser cannot read becomes null and is counted, never guessed.
- **Method:** prompt changes are tried on saved states first and built word for word as tested (a 9B's answer can flip on a small rewording). A code-only change is replayed on all 44 saved 9B states without an LLM; only the shops it can touch are rerun with the model. Saved 9B baseline: `runs/explore/v2/9B/<split>/`.
- **Open (2026-11-13):** dev_014 `customer_group ← Khách` (same column as the customer name); dev_012 `product_name` (`Làm gì`); no unit anywhere in the phone shops (dev_011, dev_014); conversions guard and a price-taken-as-factor check; step 3's field resolve; `extra_attributes` and odd structures (dev_002 toppings, dev_012 `Xe`); the write_code LLM part; `run_and_check`; `channel` / `payment_method` have no per-shop word mapping in the plan.

### Approach: the LLM writes a per-shop converter, a fixed harness checks it
*(Superseded 2026-11-12 by write_code's fixed no-LLM pipeline, above: a ≤ 9B model cannot reliably write a whole `convert()`.)*
- **Code, not a declarative mapping config.** A config (file → column → parser) cannot express what we already met: toppings split out of a drink's cell (dev_002), the `Ghi tắt` abbreviation sheet (dev_011 / 014), voucher rows (dev_013), return lines that take their date from the sale lines (biz_117 / dev_004), forward fill, subtotal rows.
- **But code constrained to our helpers.** The generated `convert(shop_dir) -> pd.DataFrame` should only call the shared library, where all the cleaning we already wrote lives:
  - `parse.py`: `is_blank`, `clean_text`, `norm_text`, `fold_accents`, `parse_money`, `parse_qty`, `parse_date`, `parse_datetime`, `strip_phone`, `customer_name_key`, `map_status`, `map_channel`, `map_payment` (and `WALK_IN_NAMES`, `STATUSES`, `CHANNELS`, `PAYMENT_METHODS`).
  - `reference/common.py`: `read_xlsx`, `read_csv`, `read_sqlite`, `drop_subtotals` (`SUBTOTAL`), `drop_repeated_headers`, `forward_fill`, `drop_duplicate_lines`, `clean_line_table` (the four cleaning steps in fixed order, inputs from the plan; the generated code calls this, not the four helpers), `readme_data_end`, `readme_conversions`, `conversion_lookup`, `match_names`, `canonical_names`, `parse_qty_and_unit`, `money`, `dates`, `implied_discount`, `returned_orders_from_negative_lines`, `Converted`.
  - `schema.py`: `SALES_LINES_COLUMNS`, `finalize`, `validate`, `manifest`, `write`.
  - Open: `common.py` probably moves out of `reference/` to a shared place (e.g. `src/datamate/convert_lib.py`), since it is no longer only for the hand-written converters.
- Few-shot examples: a few reference converters chosen to match the shop's shape (POS export with code joins: biz_108 / dev_003; hand-kept book: biz_117 / dev_004; forward fill: biz_122; café with service fee: biz_128).
- The rulebook (§1, §3, §6, §8) and the sales_lines schema table above go in the prompt: status mapping, what is revenue, what is a product, duplicates, returns.

### explore_agent (decided 2026-11-01; job written in `src/datamate/analyst_agent/explore_agent/__init__.py`)
- The explore step is its own agent, the **explore_agent**: always runs first on a shop, own prompt, own context, own tools (a LangGraph subgraph; the analyst_agent only sees its result).
- Tools: `peek(file, sheet, start, n)`, `column_values(file, sheet, column)`, `find_rows(file, sheet, text)`. The README and the file list (with real row counts) are in the agent's prompt. (Since 2026-11-02 the tools are called by the search subgraph, not by the planning LLM: see "explore_agent loop".)
- **Tools built (2026-11-01):** plain functions in `src/datamate/analyst_agent/explore_agent/explore.py`, LangGraph wrappers in `tools.py`. Conventions: a table = xlsx sheet / csv / sqlite table addressed by `(file, sheet)`; rows numbered as in the file (row 1 = first row; csv / sqlite: the column names) so `peek` and `find_rows` agree; header guessed (first of the first 10 rows with the most filled all-text cells); xlsx with `sheet=None` = all sheets; outputs capped at 6000 chars; files cached in-process. `list_files` groups tables with the same header (month sheets, split files) and lists non-data files (dev_005 / dev_008 hold `.pkl`, `load.py`) as "not read". Tried by hand with `scripts/checks/try_explore.py <split> <shop> <tool> key=value...` on biz_117, 122, 123, 128, 130, dev_002: header guess right on all (row 2 under 117's two-level header, row 3 under 122's filter row, row 4 under 130 / dev_002 title blocks); 117's four date formats, `1tr440` and negative money, 17 `Tổng tháng` footers, 122's forward-fill gaps (44,312 / 94,478 blank order codes), 128's 2,467 `Phí phục vụ` lines all visible.
- **Header rows from the README** (`explore_agent/readme.py`, 2026-11-01): 37 of 44 READMEs list columns as `## Cột trong \`File\`[, sheet \`S\`]` + `- **Col**: meaning` (file name may be a pattern `BanHang_T<tháng>_<năm>.xlsx` or the base of split files `X.xlsx` → `X_1.xlsx`). The header row = the row among the first 10 holding ≥ half the README's names; `list_files` says "matches the README's columns" or "GUESSED" and lists README columns missing from the header. `scripts/checks/check_readme_headers.py` (all 44 shops): 404 tables found from the README, the old guess agrees on all 404, every README heading matches a table, no README column missing; 98 tables have no README columns (34 sqlite + 2 csv, header = row 1 by format; 62 xlsx guessed: biz_106, 109, 124, dev_003, dev_014, dev_011 `Ghi tắt`) → LLM-fallback candidates.
- **Fixed steps before the agent (user, 2026-11-01: "the more fixed steps we can do before using the agent the better").** (As built on 2026-11-01; the graph after `prepare` and the state changed on 2026-11-02, see "explore_agent loop".) Graph then (`explore_agent/agent.py`): `START → confirm_headers → prepare → END`.
  - `confirm_headers`: one structured-output call (`HeaderChoice{header_row}`, prompt `prompts.HEADER`: first 10 rows + "our guess is row N") per group of xlsx tables that share a header and that the README does not describe; an invalid answer keeps the guess. Result in `state["header_rows"]` (`explore.table_key(file, sheet)` → row); `explore.py` functions take it as `headers` (trust order: confirmed > README > guess). No call either when the guess is row 1 and row 1 is all text (nothing above it to mistake for the header; agreed 2026-11-01). Calls per shop: 0 for shops whose README lists columns, biz_106 2, dev_003 2, dev_014 1 (only title blocks and the blank first row are left).
  - `prepare` (no LLM): reads the README + `list_files` (3.7–6k chars) into the state (`readme`, `files`); think's user prompt shows them every round (the 2026-11-01 version put them in a first message, `prompts.OPENING`, now removed). So `read_readme` and `list_files` are **not agent tools**; the tools (`explore_agent/tools.py`) are `peek`, `column_values`, `find_rows`, with `shop_dir` and `header_rows` injected.
  - State: the 2026-11-01 state had a `messages` list; it is gone (current state in "explore_agent loop"). The model goes in the runtime context `ExploreContext`.
  - Checked without an LLM: `scripts/checks/check_explore_agent.py` (fake header model, then the three tools through a real ToolNode on dev_014, then the whole loop).
- It also **makes the plan** (the plan step below is its last step), because a findings report would lose information we can't yet define. Switch to findings → planning if one agent can't do both on the 9B model.

### Plan schema and fixed cleaning (agreed 2026-11-02)
- **The explore_agent knows the shared schema** (user, 2026-11-02): its system prompt holds the sales_lines table and rulebook §1, §3, §6, §8, so it puts each raw column of *any* of the shop's files into the right schema column. The schema itself stays fixed (the query_agent's tools depend on it); what fits no column is reported (`unmapped`, `extra_attributes`), not converted, and grows the schema by hand later.
- **`SalesLinePlan`** (`explore_agent/state.py`, Pydantic, for `with_structured_output(method="json_schema")`; lists of small objects instead of free-key dicts, JSON schema 6.2k chars): `line_tables`, `columns` (schema_column ← file / sheet / raw_column), `missing_columns`, `joins`, `forward_fill_columns`, `statuses` (one `StatusWord` per raw word), `conversions` (column / file / readme / none) + `conversion_table`, `return_lines`, `service_fee_names`, `non_revenue_names`, `subtotal_labels`, `extra_attributes`, `unmapped`, `notes`. Derived columns (customer_key, product_key, line_kind, is_return_line, unit_factor, implied line_discount, source_*) are not mapped.
- **`Join`** = rows of `file` attach to rows of `on_file` where `key` = `on_key`; `on_file` may be a table joined earlier (biz_108: orders → lines by `Mã hóa đơn`, products → lines by `Mã hàng`, customers → **orders** by `Mã KH` = `Mã khách hàng`). `plan_check`: columns exist, `key` unique in `file`, most `on_key` values found.
- **Fixed cleaning, inputs from the plan** (user, 2026-11-02): `common.clean_line_table(df, order_col, order_cols, notes)` runs repeated headers → subtotals → forward fill → duplicates, always in that order (duplicates after the fill: before it, continuation lines have no order id). The explore_agent decides only the inputs: `order_col` = the raw column mapped to order_id (None = no order id), `order_cols` = `forward_fill_columns` (order id first; [] = nothing to fill). `plan_check` flags an order-id column with blanks and no forward-fill columns, and a forward-fill column without blanks (built, see "explore_agent loop"). `write_code` does not call the four helpers itself. Checked: on biz_122, dev_004, dev_005, `clean_line_table` gives the same rows as each converter's own cleaning.
- **`forward_fill` fixed** (2026-11-02): it used to fill a blank order-level cell on *every* row from the cell above, so on an unseen shop a walk-in order with a blank customer would take the previous order's customer. Now only continuation lines (blank order id) are filled, and only from their own order's first line. The output on biz_122, dev_004, dev_005 is identical to the saved references (no order-level cell is blank on a line with an order id there). Note when comparing: a datetime column is `datetime64[s]` after `schema.finalize` but `[ms]` after the parquet round-trip; align before `assert_frame_equal`.

### explore_agent loop (built 2026-11-02)
*(v1, deleted 2026-11-11; replaced by the v2 workflow above. Kept as the record of why: one reply filling all 16 plan fields translated correct notes wrongly.)*
Agreed with the user: one agent fills the **whole plan** in a loop first, like `src/impls/qna/agent.py`; test on Sonnet, then on a 9B model, and only split the plan into parts if the 9B fails. After the first runs the user agreed to split think (explore) from write_plan (write the plan): see below.
```
START → confirm_headers → prepare → think ⇄ search          (search = subgraph llm ⇄ tools, fresh per request)
                                      │ done   ↑
                                      ↓        │ problems (at most MAX_PLAN_ATTEMPTS = 3 plans)
                                 write_plan → plan_check → END (passed, or out of attempts)
```
- `think` (`agent.py`): `with_structured_output(ThinkOutput, method="json_schema", include_raw=True)`; `ThinkOutput{notes, request}`. A fresh prompt every round, rebuilt from the state: system `THINK_SYSTEM` = role (explore and write notes; the plan is written from the notes) + how to work + `prompts.SCHEMA` + rulebook §1, §3, §6, §8 (`rulebook_sections`, from `ExploreContext.rulebook`; cached), user = README + file list + notes so far + last request + report (+ after a failed check: the problems and the failed plan, `PROBLEMS`). Request → `search`; empty request = done → `write_plan` (also a request after `MAX_ROUNDS = 20`). On done, `request` and `report` are cleared, so a think after a failed check does not see an old report.
- `write_plan` (`agent.py`): `with_structured_output(SalesLinePlan, ...)`; system `WRITE_PLAN_SYSTEM` (write the plan from the notes, names exactly as written, nothing guessed: what the notes lack → missing_columns; cleaning is fixed code; SCHEMA; rulebook §1 §3 §6 §8; cached), user `WRITE_PLAN_USER` = README + file list + notes (+ `PLAN_PROBLEMS`: the problems + the last plan, "fix these, keep the rest"). No report, no tools. → `plan_check`.
- `plan_check` fails → **think** (not write_plan): think decides whether a fix needs another look at the files (request) or not (a note on the fix, done) → write_plan sees the problems.
- Unparsable reply from think or write_plan → back to the same node with "# Your last reply could not be used" (`REPLY_PROBLEM`), at most `MAX_BAD_REPLIES = 3` (`bad_replies`), then END. Routing by `Command(goto=...)`; `RECURSION_LIMIT` = 2·rounds + 3·plans + bad replies + 10 = 62 graph steps.
- `search` (`search.py`): a ReAct subgraph (`SearchState{messages, shop_dir, header_rows}`, `ToolNode` over the three tools), started fresh on each request with the request + file list; cut after ~6 tool turns (`stream` keeps what was reached). `format_report`: each tool call with its exact output (ERROR outputs and repeats left out) + the search agent's closing sentence.
- State: no message list in the parent any more (`readme`, `files`, `notes` with `operator.add`, `request`, `report`, `rounds`, `plan`, `plan_problems`, `plan_attempts`, `reply_problem`, `bad_replies`, `trace`). `ExploreContext` moved to `state.py` (the subgraph needs it), re-exported by `agent.py`.
- **`plan_check.py`** (no LLM; each problem says what is wrong and what to do): column mapping (every `MAPPABLE` column mapped or missing; `OPTIONAL` qty_base / line_discount / return_date / customer_code / line_product_name may be mapped, and may be listed as missing; `DERIVED` never; order_date and line_amount always recorded; every file / sheet / column exists; a file used must be a line table or joined; one schema column from two different raw columns is a duplicate, the same raw column once per split line file is not; `cost_price` must come from a line table); "line table" = (file, sheet) (`_is_line`), and a line-table column is read over all line tables (`_source_cells`); joins (`on_file` attached before, key unique, ≥ 50 % of `on_key` values found); statuses (every distinct raw value listed, none invented, ≤ 40 values; one word with two different statuses is a problem, 'hủy' + 'Hủy' is not); forward fill (starts with the order-id column, every listed column has blanks; > 1 % blank order ids outside subtotal rows and none listed → problem); service-fee / non-revenue / subtotal texts exist in the line tables; `return_lines` both ways (true needs negative quantities, negative quantities need true); conversions file ↔ `conversion_table`, column ↔ qty_base mapped. Checked by `scripts/checks/check_plan_check.py` (23 cases): the hand-written biz_108 plan passes and broken copies fail with the expected problem, plus cases on biz_116, 117, 122.
- `scripts/checks/check_explore_agent.py`: fake model drives the whole loop on biz_108 (think unparsable → asked again → request → real tool call → done → write_plan leaves `Đang giao hàng` out → problem → think done again → write_plan correct → passed); 13 checks: step order, think's schema has no plan, report → think, notes (not report) → write_plan, problems + last plan → think and write_plan, the trace. First prompts: think 14–20k chars, write_plan 12–18k (system ≈ 8–10k + README + files).
- Runner `scripts/run_explore.py [--split] [--model] [--workers] shop ...` → `runs/explore/<timestamp>/<shop>.json` (plan, problems, notes, `steps`, trace) + a summary line per shop. `max_tokens` 16000 (the plan is one structured reply). **Tracing (2026-11-02):** one trace record per graph step, each with seconds + token usage: confirm_headers (each check: tables, guess, answer, row used; final header rows), prepare (the file list), think (its user prompt, notes, request; or parse error + raw reply), write_plan (its user prompt, the plan; or parse error + raw reply), search (request, full transcript of LLM turns and tool outputs incl. ERRORs, `cut_by_limit`, the report exactly as think gets it), plan_check (attempt, problems). The file is rewritten after every step (`"finished": false` until the end), so Ctrl-C keeps the trace; think's and write_plan's system prompts (same every step) go once to `_run.json`. Checked in `check_explore_agent.py`.

#### Changes after the first Sonnet runs (2026-11-02)
- **think / write_plan split** (user agreed): with the plan in think's output, think twice wrote "done" notes and left both request and plan empty; it treats "done" as its own step. think now returns `{notes, request}` (empty = done) and `write_plan` writes the plan. Side gain: the plan's JSON schema no longer goes with every exploring round.
- **Retries:** an unparsable reply goes back to the same node (`REPLY_PROBLEM`, `MAX_BAD_REPLIES = 3`) instead of using up a plan attempt; the trace records each reply's `stop_reason`.
- **think's prompt: decisions, not proof** (user's call: fix why it asks, not cap tool calls). think had tried to *prove* every decision (join coverage, every column of the product list, blank counts of every column), and once asked for something no tool can do (compare two tables), which the search agent worked around with many calls. Causes: the prompt said what to find but not how much evidence is enough, nor what code already checks; and "the plan is written from your notes **alone**, so the notes must be exact". `THINK_SYSTEM` now says: notes need decisions, not proof; code checks the plan afterwards and the converter parses formats; what the three tools can and cannot show; a request = one file and one column (or one text), no sheet unless only that sheet matters (no sheet = every sheet), never "each column"; per checklist item what decides it and at most which request it needs; "most shops need 4 to 8 requests"; done = every item has a note. `SEARCH_SYSTEM`: reply in English; leave the sheet out unless the request names one; if the tools cannot do what is asked, make at most one call and say so. No caps in code.
- **plan_check false alarms removed:** OPTIONAL columns listed as missing; the same raw column mapped once per split line file; statuses read over all line files ('Đang giao dịch' only in biz_122's 2026 file); 'hủy' + 'Hủy' both listed. Added: `return_lines` false with negative quantities; `cost_price` must come from a line table (biz_116 mapped the product list's `Giá vốn`, today's cost, which is not the cost at the sale; line tables are matched by (file, sheet), since that product list is another sheet of the line workbook).
- **Plan columns:** `customer_code` and `line_product_name` added (see the scan below).
- **Proposed, not done: split files in one request.** The prompt says split files (_1, _2026) are separate files, so think asks each question once per file (biz_122: 4 extra rounds). Fix in the tools: `column_values` / `find_rows` given one file of a same-header group read the whole group (`peek` stays per file, row numbers are per file); then the prompt says one request covers the group.
- **Later:** a subtotal label must not occur inside product / customer cells (biz_117 chose `'Tổng'`, which also matches 'Cát bê tông' products and 'Tống …' customers; harmless while `clean_line_table` uses only the fixed `SUBTOTAL` regex); `find_rows` ignores accents, so "Phí" also hits 'phi 6' steel products and "Tổng" 'Tống' (reports grow); prompt caching reads only part of the time (cause not found; think / write_plan use `with_structured_output(json_schema)`, the query_agent's cached calls `bind_tools`).

#### Plan columns: scan of the reference converters (2026-11-02)
Every intermediate column the 19 reference converters build (dict keys / `df[...] =` in `src/datamate/reference/*.py`) compared with `plan_check.MAPPABLE + OPTIONAL + DERIVED` (every `schema.SALES_LINES_COLUMNS` column is already in one of them):
- **Added as OPTIONAL working columns** (read by the converter, not stored in sales_lines; `plan_check.OPTIONAL`, one line each in `prompts.SCHEMA`, `ColumnSource.schema_column` description, and the hand-written biz_108 plan, which now has 21 columns):
  - `customer_code`: `customer_key = customer_code` when the shop has codes (§3): biz_108, biz_128, dev_003, dev_006, dev_010.
  - `line_product_name`: the name written on the line, used when product_name comes from the product list and a line's code is not in it: biz_108, dev_003.
- **Already covered, no change:** `factor` / `product` (conversion lookup internals → `conversions`), `line_date` / `line_time` (biz_117, dev_004 return lines → `order_date` + `return_lines` + `return_date`), `catalog_name` / `name_key` / `raw_name` (name matching internals).
- **Name-based lookups vs `Join`** (`join_coverage.py` in the scratchpad runs `plan_check`'s join check on them): biz_116 products by `Tên hàng hóa`, dev_011 `Ghi tắt` by abbreviation, dev_013 `DanhMuc` by `Tên hàng` all pass the ≥ 50 % exact coverage, so a Join can express them; but the converter matches them accent-insensitively (`common.match_names`; dev_011 also by full name), which the Join does not say. `write_code` must use `match_names` for a join on a name column (to note in its prompt). dev_012's mechanic list (book writes "A.Đạt", list has the full name) is not a join: plan_check rejects it (0 %), correctly; the plan should map seller ← `Thợ` and say in notes that the full name comes from `DanhSachTho.xlsx`.
- **Not expressible as columns, left to the plan's `notes` + write_code:** dev_012 one ticket per row (order_id from the row), customer_key from the licence plate inside `Xe`, status always completed; phone shops and dev_013 status from `Ghi chú` with blank = completed (rulebook §8; `statuses` lists only non-blank words); phone shops' assumed unit (chiếc / cái); dev_002 toppings inside the drink's cell.

#### think and write_plan: what each is for, and a worked example
**think in one line:** each round, read the new report, write down what it found, then ask for one more thing, or leave the request empty when the notes cover everything. Output `ThinkOutput{notes, request}` (request → `search`, empty → `write_plan`).
**write_plan in one line:** from the README, the file list and think's notes, fill in the whole `SalesLinePlan` (output = the plan itself).

The prompts, collapsed (`prompts.py`):
```
think SYSTEM (same every round, cached)
  role: decide where every sales_lines column comes from, write it as notes; decisions, not proof
  code checks the plan afterwards (columns, joins, statuses, blanks, negatives, texts); converter parses formats
  the tools: some rows of one table / one column's values / rows holding one text; not two tables, not many columns
  each round: notes from the report, then a request, or an empty request = done (every item has a note)
  request rules: one file + one column (or text), no sheet unless only that sheet matters, never "each column"
  checklist 1-7, each with what decides it and at most one request: line tables → columns + joins (never
    verified) → status values → order-id blanks (forward fill) → find "Tổng" / "Phí", qty values
    → conversions → extras (from column names); "most shops need 4 to 8 requests"
  cleaning is fixed code; you only name its inputs
  # sales_lines table (SCHEMA)       # rulebook §1 §3 §6 §8
think USER (rebuilt every round)
  README, file list · notes so far · last request + report
  [after a failed check] problems + failed plan: request if a fix needs a look, else note the fix and be done
  [unparsable last reply] the problem    [out of rounds] "leave the request empty"
write_plan SYSTEM (cached): write the plan from the notes, names exactly as written, nothing guessed;
  cleaning is fixed code; SCHEMA; rulebook §1 §3 §6 §8
write_plan USER: README, file list, notes [+ problems + last plan: "fix these, keep the rest"]
```

Example on biz_108 (file, column and status names real; tool outputs illustrative):
- **think, round 1** (no notes, no report). The file list and README answer the line table, the column mapping and the joins (orders by `Mã hóa đơn`, products by `Mã hàng`, customers `Mã khách hàng` ↔ orders `Mã KH`):
  `{"notes": ["line table: DanhSachHoaDonChiTiet.xlsx", "orders: DanhSachHoaDon.xlsx joins on Mã hóa đơn; Thời gian = order_date, Trạng thái = status, Mã KH = customer_code, ...", "products: DanhSachSanPham.xlsx joins on Mã hàng; Nhóm hàng(3 Cấp) = product_group, ĐVT = base_unit"], "request": "List the values of column 'Trạng thái' in DanhSachHoaDon.xlsx"}`
- **round 2**: report = `'Hoàn thành' ×…, 'Đã trả hàng' ×512, 'Đã hủy' ×…, 'Đang giao hàng' ×12`
  `{"notes": ["statuses: Hoàn thành→completed, Đã hủy→cancelled, Đã trả hàng→returned, Đang giao hàng→in_delivery"], "request": "List the values of column 'Mã hóa đơn' in DanhSachHoaDonChiTiet.xlsx"}`
- **rounds 3–5**: 'Mã hóa đơn' has 0 blanks (no forward fill); `find_rows 'Tổng'` and `'Phí'` find nothing; 'Số lượng' min 1 (no return lines).
- **round 6**: every item has a note → `{"notes": ["no payment or cost column in any file"], "request": null}` → done.
- **write_plan**: `{line_tables, columns, joins, statuses, missing_columns: [payment_method, cost_price], conversions: "column", ...}` → plan_check.
- **Failed check** (e.g. a status word the plan left out): think's next prompt ends with "# The plan written from your notes failed the check … The plan that failed: {...}"; think requests the data if a fix needs it, else writes a note on the fix and is done → write_plan gets the problems + its last plan and fixes it.

#### think prompt: the user's view (2026-11-02) — keep it as is until tested
- The user finds the think prompt long and hard to navigate, which makes the output objective unclear; if a person struggles, a 9B model likely will too.
- Most of the length is SCHEMA and the rulebook sections: needed for the final plan, noise during the request rounds.
- Possible trims, **not to be done yet**: drop the rulebook from the system prompt (SCHEMA already carries what mapping needs); replace the 7-step checklist with a progress list (which plan fields the notes cover, which are open, so the model picks the next open one); split the plan into parts (the agreed fallback if the 9B fails).
- The think / write_plan split took the plan's JSON schema out of think's output. After the first runs the prompt was changed on what they showed (see "Changes after the first Sonnet runs"); SCHEMA and the rulebook sections are still in it, and the trims above wait for the 9B test.

### Graph (LangGraph)
*(v1 shape. Now: the v2 explore_agent → write_code (fixed stages) → run_and_check (not built); see the current design above.)*
The analyst_agent, with the explore_agent as a subgraph (its messages stay inside it; the parent reads `plan`):
```
explore_agent ─────────────────────────────────────────────→ write_code → run_and_check ─┬─ pass → manifest + brief → END
  confirm_headers → prepare → think ⇄ search → write_plan → plan_check   ↑                │
                                 ↑                  │ fail      └──── check report ────────┘  (fail; at most N tries,
                                 └──────────────────┘ (plan_problems, plan_attempts)             then give up and report)
```
Built (2026-11-02): the whole explore_agent (`confirm_headers`, `prepare`, `think`, the `search` subgraph, `write_plan`, `plan_check`); tried live on Sonnet. To build: everything after the explore_agent.

1. **explore_agent** (details in "explore_agent" and "explore_agent loop" above): fixed steps, then think ⇄ search (the search subgraph calls `peek`, `column_values`, `find_rows`), then `write_plan` → `plan_check`.
2. **plan** (the explore_agent's last step; schema now agreed: `SalesLinePlan`, see "Plan schema and fixed cleaning"; the draft below is the original idea): structured output (`with_structured_output(..., method="json_schema")`), a mapping note. Draft content:
   - which file / sheet / column feeds each schema column, and the columns the shop does not record
   - every raw status word → one of the 4 standard statuses
   - unit conversions (file, README list or none)
   - odd structure noticed: header offset, forward fill, subtotal rows, duplicates, return lines, service-fee lines, non-revenue rows, extra product attributes (brand, size, ...)

   Checked in code before any code is written (`plan_check`): every schema column is mapped or marked missing; every status word maps to a known status; every file / sheet / column named exists. A failed plan goes back to the explore_agent with the problems. Filling the plan in small parts (file roles → column mapping per file → status words → odd structure), each with its own check, is the fallback: agreed 2026-11-02 to fill the whole plan in one loop first and split it only if the 9B model fails.
3. **write_code**: the LLM writes `convert(shop_dir)` from the plan, the helpers and the examples.
4. **run_and_check**: a plain-Python node (no LLM). Runs the code in a subprocess (UTF-8 stdout, timeout), then key-free checks:
   - `schema.validate` (types, enums, required columns, no `nan` / `None` text)
   - every raw status is mapped; no unparsed money / date / qty cells (the helpers raise instead of guessing)
   - dates within the README's data range; `qty_base = qty × unit_factor`; `line_amount ≈ qty × unit_price − line_discount`
   - subtotal rows / order totals in the export match the converted lines (as `scripts/checks/check_subtotals.py` does)
   - sanity: line count vs raw row count, no product key that is a subtotal label, orders per day plausible

   A failure goes back to `write_code` as a short report (which check, a few example rows), and the agent fixes its code.
5. **manifest + brief**: `schema.write`, plus the `brief` lines (how an odd shop maps onto sales lines) that the query_agent shows in its shop brief. Rule from public test run 1: the brief must not call data OUT OF SCOPE just because it was not mapped.

New LangGraph concepts this brings, one at a time for the user: a retry loop with a counter in the state; a node that runs code instead of calling the LLM; structured output for the plan; a subgraph. `ToolNode`, `InjectedState`, `Command` and the runtime context are already known from the query_agent, and the think ⇄ search pattern from `src/impls/qna`. Used so far in the explore_agent (2026-11-02): the retry loop (`plan_check` routes back to `think` with `Command(goto=...)` while `plan_attempts` < 3; no conditional edge needed), the plain-code node (`plan_check`), structured output with `include_raw=True` (raw reply for token usage; a bad reply becomes a parsing error instead of an exception), the search subgraph run from inside a node, and `recursion_limit` raised for long loops.

### Benchmark (built before the agent)
- `scripts/bench_analyst.py` (name to be decided): for each shop with a hand-written reference, compare the agent's parquet with `data_std/<split>/<shop>/`:
  - line count, order count, revenue and orders per month, status counts, qty_base per product, set of product keys, columns available / missing
  - output: a per-shop score + the first differences, so it shows which step went wrong
- Shops: 5 train (biz_108, 116, 117, 122, 128; also scorable through the answer key) + 14 public test (dev_001–dev_014). Hold some out from prompt examples to measure generalization.
- Then end to end: analyst_agent on the ~25 unconverted train shops → query_agent → scored against `answers.jsonl` (`scripts/run_agent.py`, user runs it).

**Built (2026-11-01):** `src/datamate/bench.py` (`compare(cand, ref, cand_manifest, ref_manifest)` → 17 `Check`s with score 0–1 + first differences: line_count, order_ids, columns_available, data_end, column_fill, status_counts, order_status, revenue_by_month / _day, orders_by_month, product_keys, qty_base_by_product, line_kinds, product_groups, customer_keys, validate; shop score = mean). `scripts/bench_analyst.py [--split] [--candidate DIR] [--label X] [--self] [--verbose] [shop ...]`: candidate `data_std_agent/<split>/<shop>/` vs reference `data_std/<split>/<shop>/`; missing candidate = `missing`, score 0; writes `runs/bench/<timestamp>[-label].json` and shows each shop's change vs the previous JSON of the split (`--self` runs are not written). Checked: `--self` = 1.000 on all 19 shops; `scripts/checks/check_bench.py` breaks references in memory (subtotals kept, no forward fill, duplicates kept, cancelled → completed, no unit conversion, column left unmapped): 6/6 caught by the expected checks.

### Build order
*(v1 order, as of 2026-11-02. Since then: v2 explore_agent built step by step (2026-11-10 → 13), write_code stages 1–8 built and benched (2026-11-12 → 13); next work in `explore_agent.md`.)*
1. Done: the benchmark script (above).
2. Done: explore tools, README header extraction, the explore_agent's fixed steps (`confirm_headers`, `prepare`), state, tool wrappers; checked without an LLM.
3. Done (2026-11-02): the plan schema `SalesLinePlan` and `common.clean_line_table` (see "Plan schema and fixed cleaning"). Whole plan in one loop first; in parts only if the 9B model fails (agreed 2026-11-02).
4. Built (2026-11-02, see "explore_agent loop"; tried on Sonnet on biz_108, 116, 117, 122): think's system prompt (rulebook §1, §3, §6, §8 + the sales_lines schema + the investigation order), the think ⇄ search loop (search = a ReAct subgraph over the three tools), `write_plan` (split from think), `plan_check` with its retry loop (counter in the state), and per-step tracing in `scripts/run_explore.py`. Next (user runs it; loads `.env`): biz_128 and the public-test references, then the hard ones (forward fill, books, sqlite, README without columns: 106, 109, 123, 124).
5. Key-free checks as a function (reused by `run_and_check` and the bench).
6. `write_code` + `run_and_check`, then the analyst_agent graph around the explore_agent subgraph. Output to `data_std_agent/<split>/<shop>/` (decided 2026-11-01), so it never overwrites the references; score with `scripts/bench_analyst.py`.

### Open questions
*(As of 2026-11-02. Answered since: v1's prompt length and whether a 9B can write a whole converter (no: v2 workflow + write_code's fixed pipeline); the model rule (≤ 12B in total). Still open: the ones below on `common.py`, bench weighting, give-up behaviour, the model choice, extra attributes.)*
- Where `common.py` lives (above).
- Bench weighting: all checks weigh the same, so a column left unmapped costs only ~0.005 of the shop score (two checks drop) although it turns questions into wrong `unanswerable`s. Read the per-check lines, not just the mean.
- How many retries (plan check and code check), and what happens on give-up (no output → the query_agent abstains on that shop's questions?).
- Which ≤ 9B model, and how it is served (decides tool calling and structured-output support).
- Whether think's prompt (14–20k chars) and write_plan's (12–18k + the ~6k-char plan JSON schema) are too long for the 9B model; possible trims are listed in "think prompt: the user's view" and wait for the 9B test.
- Whether `write_code` (a whole converter) is feasible for a ≤ 9B model; if not, part of plan → code may become a fill-in template run by fixed code.
- Extra product attributes (generic `attr:*` columns, "How the tools and file types grow") need a schema change first: the agent should write them, and the query_agent should filter / group on them.

## Earlier next step (superseded by the section above)
Design the sales_lines retrieval tools: list the train question intents that sales_lines can answer (by sample), then the tool parameters that cover them. Questions needing other tables (e.g. 122 "revenue of drugs with active ingredient X" needs the drugs table) are out of scope for now.

Open questions:
- (Answered 2026-10-28: event tables become their own file types with their own finder; cross-table questions call several finders and combine them in `calculate`.) How to handle the long tail (comparisons across periods, inventory = imports − sales, cross-table questions): more tools, fall back to the earlier code-writing prototype, or abstain? (Wrong answers cost −1, so abstain is a real option.)
- (Done in step 6: questions run concurrently in `run_agent.py`, and the system prompt with the rulebook is cached.)

## Environment notes
- Windows. Run Python with `.venv/Scripts/python.exe` (pandas 3.1.0, openpyxl, pyarrow, langgraph, langchain-anthropic are installed; the project uses uv, the venv has no pip). Use `-I` when running code that reads dataset files. `-I` drops the project root from `sys.path`, so scripts in `scripts/` insert it themselves before `from src.datamate ...` (see `scripts/convert_reference.py`).
- Generated data: `data_std/<split>/<shop>/` (parquet + manifest). Regenerate with `scripts/convert_reference.py [--split public_test]`; it is not hand-edited.
- Bash output must be redirected to `./cmd_output.txt` and read back with the Read tool (user's global CLAUDE.md).
- `-I` also ignores `PYTHON*` environment variables, so `PYTHONIOENCODING=utf-8` has no effect: a script that prints Vietnamese or `→` must call `sys.stdout.reconfigure(encoding="utf-8")` itself (the console is cp1252).
- Never read `.env`. Ask before running scripts that load it (`zalo_main.py`, `scripts/run_agent.py` and the `scripts/explore/` runners that call the model do).
- The user runs the live runs and check scripts; give the commands and wait for the report. Run results go in the reply, not in this file or `explore_agent.md`.
- Generated agent output: `data_std_agent/<split>/<shop>/` (never over the references).
- The user is new to LangGraph: introduce one concept at a time and point out LangGraph/LangChain built-ins (`InjectedState`, `ToolNode`, `with_structured_output`, `recursion_limit`, ...).
- If a package is missing, pause and ask the user to install it.
