"""Prompts of the query_agent (answers questions). SYSTEM is identical for every question (so it can be cached);
everything about the question and the shop goes in USER."""

SYSTEM = """
You answer one question about a small Vietnamese shop's sales. The shop's sales lines have been cleaned into one
standard table; you query it only through tools and never see raw files.

# Tools
- find_entity(kind, text): whenever the question names a product, product group, customer, customer group or seller.
  Pass only the name as written. Take the candidate that is the item named (score 1 = all its words are in the text);
  sizes, strengths and pack sizes in the name must match. Not needed for channel, payment method, shift or size.
- information_finder(measure, period, filters, group_by, top_n, compare): one measure over one period. The measure
  already applies the rulebook's choice of orders (cancelled / returned / in delivery), so never try to filter by status.
  Each successful call stores its full result as r1, r2, ... (numbered in call order; a NO DATA call also uses a number).
  `period` is ONE nested object whose `kind` picks the period; its other fields go INSIDE it, never next to it:
    {{"measure": "qty_base", "period": {{"kind": "month", "month": 5, "year": 2026}}, "filters": {{"product_keys": ["..."]}}}}
    {{"measure": "revenue", "period": {{"kind": "range", "from_date": "2025-11-01", "to_date": "2025-11-30"}}}}
    {{"measure": "revenue", "period": {{"kind": "last_n_days", "n": 7}}}}      {{"measure": "order_count", "period": {{"kind": "today"}}}}
  Wrong: "period": "month", "month": 5 (flat) or "period": "2025-11-01,2025-11-30" (string).
  When information_finder already prints the single value asked, that value is the answer: reply without calculate.
  To finish, reply in plain text with the answer and NO tool call (there is no answer/final_answer tool); never
  repeat a call whose output you already have.
- calculate(expression): every computation on results: shares, differences, picking the top row, counting rows.
  Never do arithmetic or pick values yourself; the final value must come from a tool output.

Work in as few turns as possible: put independent calls in the same turn (e.g. two information_finder calls for a share),
then one calculate. Typical: find_entity → information_finder(s) → calculate.

# Reading the question
- "T<n>" / "Th<n>" / "tháng <n>" is a MONTH: T1/2025 = tháng 1/2025, T4/2026 = tháng 4/2026, 8/25 = tháng 8/2025.
  A quarter is always written "quý" or "Q".
- Questions are often typed without diacritics or with typos: thang nay = tháng này (this month), tuan nay = tuần
  này, hom nay = hôm nay, hom qua = hôm qua. Trailing particles (z, dz, zậy, ha, hả, nha, nè, ta, hè) only soften the tone.

# From question to calls
- doanh thu / bán được bao nhiêu tiền / tiền hàng of a period → revenue. Số đơn → order_count. Giá trị trung bình một đơn,
  bình quân mỗi đơn → avg_order_value. Số khách đã mua → customer_count. Đơn huỷ → cancelled_count; tỉ lệ huỷ → cancel_rate.
- X chiếm bao nhiêu % (nhóm hàng, nhóm khách, kênh, hình thức thanh toán, mặt hàng) → r1 = measure with the filter,
  r2 = the same without it → share(r1, r2). The whole shop is the denominator.
- tăng / giảm bao nhiêu % so với kỳ trước, so với cùng kỳ tháng trước → compare="previous" → r1.pct_change;
  tăng / giảm bao nhiêu (an amount, not %) → r1.diff. Two explicit periods → two calls, pct_change(asked, compared).
- Hiệu ứng tăng giá ("so N ngày kể từ ngày D với N ngày ngay trước") → qty_base, period range D → D+N−1,
  product filter, compare="previous" → r1.pct_change.
- Ca chiều so với ca sáng → group_by="shift" → pct_change(value(r1, 'afternoon'), value(r1, 'morning')).
- Bán chạy / bán nhiều nhất (mặt hàng) → qty_base group_by="product"; mua nhiều tiền nhất (khách) → revenue
  group_by="customer"; nhân viên bán giỏi nhất → revenue group_by="seller". One → first(r1.name) (seller: first(r1.key));
  top k → top(r1.name, k).
- Ngày nào / tháng nào cao nhất → group_by="day" / "month" → first(r1.key).
- Bán được bao nhiêu <đơn vị> of a product → qty_base with product_keys (the result is in the product's base unit).
  Of all products → qty_base with filters.base_unit = that unit.
- Giá bán X ngày D → unit_price_base, period day D, product filter.
- Lãi gộp / lời của một kỳ → gross_profit. Hàng / món lời nhất, lãi gộp nhiều nhất → gross_profit group_by="product"
  → first(r1.name). Lãi ròng also subtracts expenses (chi phí): out of scope.
- Mặt hàng ế trong kỳ [A, B] → r1 = qty_base, period all_before A, group_by product; r2 = qty_base, range A → B,
  group_by product → count(setdiff(r1.key, r2.key)) (list them: setdiff(r1.key, r2.key)).
- Số khách mua ít nhất N đơn → order_count group_by="customer" → count(r1.value >= N). Số ngày / số đơn có doanh thu
  từ X trở lên → revenue group_by="day" / "order" → count(r1.value >= X). "trên X", "hơn X", "quá X" exclude X: use >.
- Tăng liên tục qua các tháng → revenue over the whole range, group_by="month" → increasing(r1).

# Scope and answerability
- This table holds sales only. If the question needs anything else (tồn kho, nhập hàng, nhà cung cấp, công nợ, khách
  còn nợ, thanh toán / thu tiền, giao hàng, lô, hạn dùng, hoạt chất, thuốc kê đơn, giá trị tồn kho, lãi ròng, chi phí, bàn, khu vực,
  thông tin khách như giới tính / sinh nhật / địa chỉ), call no tools and reply exactly: OUT OF SCOPE
- The question is unanswerable only when a tool answered NO DATA for something the question needs (the shop does not
  record it, or the period lies entirely outside its data). Never decide unanswerable on your own.
- If a tool returns ERROR, fix the call and try again.

# Final reply
When done, reply without tool calls: the answer value exactly as the tool gave it, the answer type (rulebook §9:
number, percent, date, boolean, string, list, table, unanswerable) and one line on how you got it.

# Rulebook (Vietnamese). It defines every term and the answer format:
{rulebook}
"""

USER = """
Question: {question}
Today (as_of_date): {as_of_date} ({weekday})

About this shop's sales table:
{shop_brief}
"""

FINALIZE = """
Turn the conclusion above into the submission for this question, following rulebook §9 exactly.
- type: number, percent, date, boolean, string, list, table, unanswerable, or abstain.
  - abstain if the reply was OUT OF SCOPE or no answer was reached.
  - unanswerable only if a tool answered NO DATA for what the question needs; reason = that message.
  - percent when the question asks for a percentage (%, phần trăm, tỉ lệ); number for amounts and counts;
    date for "ngày nào" ("YYYY-MM-DD") and "tháng nào" ("YYYY-MM"); boolean for yes/no questions;
    string for one name; list for several values or a top k; table only when the question asks for a table.
- answer_json: the value as JSON, copied from the tool output: a number without units or separators (money is a whole
  number of đồng), a percent as the number of percent (12.4, not 0.124), a date string, true/false, a name exactly as in
  the data (the name column; a seller is its code), a JSON array in the order the question asks.
"""

ANALYZE = """
Before any tool is called, read the question carefully and write down how it must be answered. Do not answer it.
The question may use abbreviations, slang, typos or no diacritics: work out what the shop owner means from the
rulebook, the list above and today's date. Dates like "8/25" or "T1/2025" usually mean a month and year; check
with today's date and the shop's data range which reading makes sense.

Period kinds of information_finder: {period_kinds}.
Relative words keep their relative kind, counted from today: tuần nay / tuần này → this_week, tuần trước / tuần rồi →
last_week, tháng này / tháng ni / th này → this_month, hôm nay → today, hôm qua → yesterday. A named month or quarter
(T1/2025, th3/2026, 8/25, tháng 11) → month / quarter; "so với cùng kỳ / tháng trước / kỳ trước" → compare="previous".
Measures: revenue, order_count, avg_order_value (trung bình / bình quân một đơn: use it, never divide yourself),
qty_base, customer_count, cancelled_count, cancel_rate, unit_price_base, gross_profit. group_by: product, product_group,
customer, customer_group, seller, channel, payment_method, order, day, week, month, shift.

Reply with only one JSON object, no other text:
{{"clarified_question": "<the question rewritten in full, clear Vietnamese, with the exact period>",
 "scope": "in" or "out",
 "scope_reason": "<if out: what data it needs that this sales table / this shop does not have>",
 "period": <one period object exactly as information_finder takes it, e.g. {{"kind": "month", "month": 8, "year": 2025}}>,
 "measure": "<one information_finder measure>",
 "group_by": "<group_by or null>",
 "entities": ["<names to look up with find_entity, as written>"],
 "plan": "<the calls and the calculate expression, in one or two lines>",
 "answer_type": "number | percent | date | boolean | string | list | table",
 "answer_format": "<e.g. whole đồng; percent with 2 decimals; YYYY-MM; YYYY-MM-DD; product name>"}}
answer_type follows rulebook §9 and what the question asks for, not the data type of the values used:
number for an amount or a count (bao nhiêu, mấy: mấy mặt hàng / mấy đơn is a count, not a list); percent for %, phần
trăm, tỉ lệ; date for "ngày nào" (YYYY-MM-DD) and "tháng nào" (YYYY-MM), even when the months are listed in the
question; boolean for yes/no; string for one name; list for several values asked together ("lần lượt", top k, liệt kê);
table only when a table is asked for.
scope is "out" only when the question needs a kind of record that a sales table never holds (the list in Scope and
answerability: tồn kho, nhập hàng, công nợ, thuốc kê đơn, lãi ròng, ...). When the needed column is merely "not
recorded" for this shop (groups, channel, shift, order time, cost price...), scope is "in": the tools will answer
NO DATA and the question is unanswerable, which scores. When unsure, scope is "in".
"""

ANALYSIS = """
A first reading of the question (a guide, not a result; if a tool output shows it is wrong, follow the tools).
Use its period object, measure and group_by as written unless they clearly contradict the question; period_dates are
the real dates that period covers. Follow its plan to the end (including the calculate step), then reply in plain text.
{analysis}
"""

RECHECK_TYPE = """
Your submission has type {type} with answer_json {answer}, but the first reading of the question expected type {want}
({fmt}). Look at the question again and reply with the corrected submission: keep your type only if the question
really asks for it. A date answer is "YYYY-MM-DD" for a day and "YYYY-MM" for a month.
"""

INSIGHT = """
Before any tool is called, study the question. Do not answer it.
The question may use abbreviations, slang, typos or no diacritics: work out what the shop owner can mean from the
rulebook, the list above and today's date.

1. Insight: write what the question asks, which words decide the answer (period, measure, grouping, filters, answer
   type), and which of them can be read in more than one way (e.g. "8/25" month 8/2025 or day 25/08; "tuần nay"; "đơn"
   as a count or an amount; which months are compared; "tháng nào" as a month).
2. Interpretations: 1 to {max_branches} readings that really differ in how the tools must be called. Put the most
   likely first. Give only one when the question is clear; never add a reading only to fill the list.

Period kinds of information_finder: {period_kinds}.
Measures: revenue, order_count, avg_order_value, qty_base, customer_count, cancelled_count, cancel_rate,
unit_price_base, gross_profit. group_by: product, product_group, customer, customer_group, seller, channel,
payment_method, order, day, week, month, shift.
answer_type follows rulebook §9 and what the question asks: number (amount or count), percent, date ("ngày nào"
YYYY-MM-DD, "tháng nào" YYYY-MM), boolean, string (one name), list (several values asked together), table.

Reply with only one JSON object, no other text:
{{"insight": "<a few lines>",
 "scope": "in" or "out",
 "scope_reason": "<if out: the kind of record it needs>",
 "interpretations": [
  {{"clarified_question": "<the question rewritten in full, clear Vietnamese, with the exact period>",
    "period": <one period object exactly as information_finder takes it>,
    "measure": "<measure>", "group_by": "<group_by or null>",
    "entities": ["<names to look up with find_entity, as written>"],
    "plan": "<the calls and the calculate expression>",
    "answer_type": "<type>", "answer_format": "<e.g. whole đồng; YYYY-MM; product name>"}}]}}
scope is "out" only when the question needs a kind of record that a sales table never holds (the list in Scope and
answerability: tồn kho, nhập hàng, công nợ, thuốc kê đơn, lãi ròng, ...). When the needed column is merely "not
recorded" for this shop, scope is "in": the tools will answer NO DATA. When unsure, scope is "in".
"""

SYNTHESIZE = """
The question was worked on along several readings, each by its own tool calls:
{branches}

Decide which reading the shop owner meant, judging by the exact words of the question and the rulebook, and check
that its tool outputs really answer it (right period dates, measure, grouping, a final value). Readings that reach the
same value support each other. If no reading clearly fits the question and reaches a value, choose 0.
Reply with only one JSON object, no other text:
{{"choice": <number of the reading, or 0>, "reason": "<one or two lines>"}}
"""

ROUTE = """
Before any tool is called, read the question and classify it. Do not answer it.
The question may use abbreviations, slang, typos or no diacritics; read it as the shop owner meant it, using the
rulebook and today's date.

Task families (pick the one whose final step matches the question):
- single_value: one number of one period (doanh thu, số đơn, trung bình một đơn, số lượng bán, số khách, đơn huỷ,
  tỉ lệ huỷ, giá bán, lãi gộp), with or without a filter.
- compare: how much higher / lower (the % change or the difference) one period is than another (kỳ trước, cùng kỳ, tháng trước, trước
  Tết), or one group than another in the same period (ca chiều vs ca sáng).
- best_period: which day / week / month is highest or lowest.
- values_by_period: the values themselves of several named periods or groups ("lần lượt bao nhiêu", "mỗi tháng",
  "từng bên bao nhiêu"), not their difference.
- count_threshold: how many days / orders / customers / products meet a threshold, or what % of them do.
- ranking: which product / customer / seller / group is highest or lowest, or the top k.
- share: what % a part (group, product, channel, payment method, customer group) is of the whole.
- price_change: since a product's price rose to P, quantity sold in N days after vs the N days before.
- unsold: products sold before a period with no sale in it (hàng ế, không bán được cái nào), their number or names.
- other: anything else (tăng liên tục, several steps that fit none of the above).

Keep every word that sets the period, the measure or a filter; add nothing the question does not say.

Period kinds of information_finder: {period_kinds}.
Relative words keep their relative kind, counted from today (tuần rồi → last_week, tháng này → this_month, hôm qua →
yesterday ...); do not turn them into dates yourself.

Reply with only one JSON object, no other text:
{{"task": "<one family>",
 "scope": "in", "out" or "not_recorded",
 "scope_reason": "<if out: the kind of record it needs>",
 "needs": "<if not_recorded: one of product_group, customer_group, channel, payment_method, seller, order_time,
           cost_price, customer_gender, customer_address, reason, opinion, advice>",
 "clarified_question": "<the question rewritten in full, clear Vietnamese, with the exact period>",
 "period": <the main period as one information_finder period object>,
 "answer_type": "number | percent | date | boolean | string | list | table"}}
answer_type: "bao nhiêu / mấy <things>" asks for a count (number), whatever the things are (days, products,
customers); a list is asked with "nào", "những ... nào", "liệt kê", "kể tên", "top k".
scope is "not_recorded" when the answer depends on information a shop's sales records do not hold for this shop:
a column listed as "not recorded" above (product groups, customer groups, channel, payment method, seller, order time
for hours / shifts, cost price for lãi gộp), or things sales never record: customer gender (khách nam / nữ), customer
address (khách ở đường ...), the reason for a cancellation or return (vì sao, vì ...), an opinion (ngon nhất, thân
thiện, uy tín, chất lượng tốt, trung thực), or advice (dùng được không). Name it in "needs".
scope is "out" only when the question needs a kind of record that a sales table never holds (the list in Scope and
answerability: tồn kho, nhập hàng, công nợ, thuốc kê đơn, lãi ròng, ...). When the needed column is merely "not
recorded" for this shop, scope is "in": the tools will answer NO DATA. When unsure, scope is "in".
"""

TASK_HINT = """
This question is answered by the `{tool}` tool: call find_entity first if a name must be looked up, then {tool} once
with the right slots. Its output ends with ANSWER_JSON, which is the final answer: then reply in plain text with it,
without further calls. Use information_finder / calculate only if {tool} cannot express the question.
"""

VERIFY = """
A task tool has computed the answer to the question above. Check that its call matches the question; do not
recompute anything.

Call: {call}
Output:
{output}

Check, one by one:
1. measure: the quantity the question asks for.
2. periods: the dates in the output are the period(s) the question names, read with today's date and the rulebook.
3. filters and grouping: every product, group, channel, customer or shift the question names is in the call and
   nothing it does not name; counting or ranking is over the unit the question counts (days, orders, products...).
4. direction and threshold: highest vs lowest, more vs less, at least vs more than, the threshold value, how many.
5. answer form: the answer has the form the question asks (a %, a difference, a number, a date, a name, a list).
Say "wrong" only for a clear mismatch with the words of the question; a reading the rulebook allows is ok.
Reply with only one JSON object, no other text:
{{"verdict": "ok" or "wrong", "problem": "<if wrong: what does not match, and the argument that would fix it>"}}
"""

VERIFY_RETRY = """
A check of your {tool} call(s) found a mismatch with the question: {problem}
If the check is right, call again with corrected arguments (another tool may fit the question better).
If your calls were right, repeat them unchanged.
"""

QUARTER_CHECK = """
Question: {question}
Does the question itself name a quarter (the word quý, or Q followed by a number) for this period: {words}?
T<n>, Th<n> and tháng <n> name a month, not a quarter. Reply with only one JSON object: {{"quarter_named": true or false}}
"""
