# Hướng tối ưu mới cho Qwen3.5-9B: phân tích và kế hoạch (2026-11-12)

Điểm hiện tại: **0.57** (flow nhánh). Mục tiêu: tối ưu cho chính model 9B, không bám theo cách làm của Sonnet, và **không làm hỏng các câu đang đúng**.

Dữ liệu: 4 lần chạy full public_test (v2, v3, v4, nhánh), so với bản Sonnet bằng `grading.py`. Script ở `reports/scripts/` và scratchpad.

## 1. Phát hiện chính

### 1.1 Phần lớn lỗi là ngẫu nhiên, không phải do model không hiểu

| Chỉ số | Số câu / 1000 |
|---|---|
| Đúng ở **cả 4** lần chạy | 679 |
| Đúng ở **ít nhất 1** lần chạy | 932 |
| v3 → v4 (chỉ khác vài dòng prompt): đúng thành sai | 75 |
| v3 → v4: sai thành đúng | 72 |

- 9B **làm đúng được** khoảng 93% số câu, nhưng mỗi lần chạy chỉ đúng khoảng 65–68%.
- Khoảng 250 câu đổi kết quả giữa các lần chạy. Ví dụ "T10/2025 quán bán được bao nhiêu tiền": cả 4 lần đều gọi tool giống nhau, nhưng có 1 lần finalize lại abstain.
- **Đây là lý do khi sửa prompt, các câu đúng trước đó bị hỏng (ý 4).** Một phần lớn không phải lỗi prompt mà là nhiễu sampling: mỗi lần chạy, khoảng 70 câu đổi kết quả theo cả hai chiều. So sánh 1 lần chạy với 1 lần chạy thì không phân biệt được prompt tốt hay xấu.
- `run_agent.py` không set temperature, nên vLLM dùng `generation_config.json` của model, tức là sampling. Thinking mode của Qwen thường dùng temperature khoảng 0.6–1.0. Điều này cần kiểm chứng khi pod chạy lại.

### 1.2 Điểm mất theo dạng câu hỏi

Dạng câu được gán từ cách Sonnet gọi tool, chỉ dùng để phân tích. "Mất" là số điểm thiếu so với tối đa, tính trung bình 4 lần chạy. Tổng mất khoảng 247 điểm.

| Dạng câu | Số câu | Đúng cả 4 lần | Lúc đúng lúc sai | Luôn sai | Điểm mất |
|---|---|---|---|---|---|
| So sánh % / chênh lệch (so kỳ trước, ca chiều/sáng, cùng kỳ) | 107 | 48 | **51** | 8 | **36.5** |
| Ngày nào / tháng nào cao nhất, liệt kê từng tháng | 52 + 14 | 33 | 17 | 16 | **44** |
| Đếm theo ngưỡng (bao nhiêu ngày/đơn/khách/mặt hàng ≥ X, % khách ≥ N đơn) | 90 | 58 | 21 | 11 | **29.8** |
| Câu lẽ ra OUT OF SCOPE (−1 khi trả lời) | 217 | — | — | — | **27** |
| Xếp hạng (bán chạy nhất, top k, NV/khách giỏi nhất) | 70 | 43 | 22 | 5 | **23** |
| Một con số đơn giản (doanh thu / số đơn / TB đơn / số lượng) | 345 | 265 | 63 | 9 | **~50** |
| Tỉ trọng %, Tết, hàng ế, xu hướng | 51 | 25 | 24 | 2 | ~15 |

- Dạng câu đơn giản nhất vẫn mất khoảng 50 điểm, gần như toàn bộ do nhiễu: lúc đúng lúc sai, rất ít câu luôn sai.
- Các dạng nhiều bước (so sánh, đếm ngưỡng, chọn ngày/tháng, xếp hạng) mất khoảng 130 điểm. Đây là nơi 9B phải tự ghép `information_finder` + `calculate` + chọn kết quả, nên dễ sai một bước.

### 1.3 Có giá trị đúng trong output của tool nhưng đáp án cuối vẫn sai

Ở bản v2, có **37 câu lệch và 17 câu abstain** mà output của tool **đã chứa** đúng đáp án của Sonnet. Model chọn sai dòng, sai type (`"2025-08-30"` nhưng type `string`), hoặc finalize abstain. Đây là lỗi ở **bước chọn và đóng gói đáp án**, không phải ở bước hiểu câu hỏi.

## 2. Nhận định: vì sao các vòng sửa prompt vừa rồi không hiệu quả

1. **Đo sai cách.** So 1 lần chạy với 1 lần chạy, trong khi nhiễu khoảng ±70 câu, nên không biết thay đổi nào thật sự có ích.
2. **Prompt chung ngày càng dài.** Thêm một chỉ dẫn cho dạng câu A thì 9B cũng đọc nó khi làm dạng B. Prompt càng dài, 9B càng dễ lệch.
3. **9B phải tự làm quá nhiều bước tự do:** chọn measure, tạo period object, group_by, viết biểu thức `calculate`, chọn dòng, rồi viết JSON đáp án. Mỗi bước có xác suất sai nhỏ, nhưng câu nhiều bước thì xác suất sai cộng dồn.

## 3. Hướng đề xuất

Nguyên tắc: **giảm số quyết định 9B phải tự đưa ra, và cô lập các thay đổi theo dạng câu.** Không đổi schema, không xoá code; chỉ thêm step, tool và prompt.

### 3.1 Bước phân loại câu hỏi (router), thay cho insight/nhánh

Một lượt LLM ngắn, chỉ chọn **một trong khoảng 8 dạng câu** kèm các slot cơ bản: kỳ, đối tượng, ngưỡng. Prompt router mô tả mỗi dạng bằng 1 dòng tiếng Anh và 1 ví dụ câu hỏi (tiếng Việt), không chứa trường hợp đặc biệt. Có thêm dạng "khác" để đi luồng cũ.

### 3.2 Tool theo dạng câu (task tools)

Mỗi tool nhận ít tham số, tự làm hết các bước nhiều bước và **trả về đáp án cuối kèm type**. Bên trong, các tool này dùng lại `finder` và `calc` có sẵn.

| Tool mới | Thay cho chuỗi bước hiện tại |
|---|---|
| `compare_periods(measure, period, against, filters, group_a/group_b)` | so kỳ trước / cùng kỳ / ca chiều với ca sáng → pct_change hoặc diff |
| `best_period(measure, period, by=day/month, highest/lowest)` | ngày/tháng nào cao nhất → trả date `YYYY-MM(-DD)` |
| `values_by_period(measure, periods[])` | "lần lượt bao nhiêu" → list |
| `count_where(measure, period, per=day/order/customer/product, op, threshold, as_percent)` | đếm theo ngưỡng / % khách ≥ N đơn |
| `rank(measure, period, by, k, filters)` | bán chạy nhất / top k / NV giỏi nhất → string hoặc list |
| `share(measure, period, part_filters)` | tỉ trọng % |
| `slow_movers(...)`, `tet_compare(...)` | hàng ế, Tết so với trước Tết |

`information_finder`, `calculate` và `find_entity` giữ nguyên cho dạng "khác" và câu đơn giản.

### 3.3 Prompt riêng cho từng dạng, ngắn, viết bằng tiếng Anh

- Agent chỉ thấy **tool và 1–2 ví dụ của đúng dạng câu đó**, cộng phần chung tối thiểu (rulebook, cách đọc kỳ).
- Sửa prompt của dạng "so sánh" không ảnh hưởng dạng "xếp hạng". Đây là cách trực tiếp nhất để tránh làm hỏng các câu đang đúng (ý 4).
- Chỉ dẫn viết bằng tiếng Anh như hiện tại (chốt 2026-11-12). Câu hỏi, rulebook và ví dụ câu hỏi vẫn giữ tiếng Việt. Cải thiện prompt (ý 1) theo hướng ngắn, rõ, chung cho cả dạng câu, không vá từng case.

### 3.4 Đáp án được dựng từ kết quả tool (finalize theo code)

Khi task tool đã trả `{type, answer}`, submission lấy thẳng từ đó, không qua LLM finalize. Cách này xử lý nhóm §1.3 (khoảng 40–50 câu) và lỗi type date. Những câu đi luồng cũ vẫn dùng finalize hiện tại.

### 3.5 Giảm nhiễu

- **Decoding:** thử temperature thấp, hoặc tắt thinking cho các lượt chỉ cần chọn tham số tool. Đo xem độ ổn định có tăng không.
- **Chạy nhiều lần rồi lấy đa số, chỉ ở bước router và tham số tool** (rẻ, vì tool chạy cục bộ). Phép thử offline trên 3 lần chạy full cho thấy lấy đa số 2/3 tăng từ 0.53 lên **0.61** (proxy).

### 3.6 Scope gate dựa vào router

Router có dạng "out of scope". Mô tả của nó là danh sách loại dữ liệu (nhập hàng, tồn kho, công nợ...), như prompt hiện có, không liệt kê từ khoá cứng. Đi kèm là quy tắc giữ nguyên từ v2: thiếu cột thì vẫn là "in" để tool trả NO DATA.

## 4. Cách đo, để không làm hỏng câu đúng

1. **Tập dev có đáp án thật:** 591 câu train thuộc 5 shop đã convert, chấm bằng `answers.jsonl`. Public test so với Sonnet chỉ dùng để tham khảo.
2. **Bảng điểm theo từng dạng câu** sau mỗi thay đổi. Chỉ nhận thay đổi khi dạng mục tiêu tăng **và không dạng nào khác giảm quá mức nhiễu**.
3. **Mỗi cấu hình chạy ≥ 2 lần**, hoặc dùng decoding ổn định, để so trung bình thay vì một lần chạy.
4. **Tập hồi quy cố định:** 679 câu đúng cả 4 lần. Câu nào ở đây chuyển thành sai là cảnh báo đỏ.

## 5. Thứ tự thử nghiệm đề xuất

| Bước | Nội dung | Rủi ro | Kỳ vọng |
|---|---|---|---|
| 0 | Dựng lại pod; đo nhiễu: chạy cùng code 2 lần, thử temperature thấp | thấp | biết mức nhiễu thật, có thể +0.02–0.05 nhờ ổn định |
| 1 | Router + 3 task tool lớn nhất (`compare_periods`, `best_period`/`values_by_period`, `count_where`) + đáp án từ tool | trung bình | các dạng này đang mất khoảng 110 điểm; lấy lại một nửa là khoảng +0.05 |
| 2 | `rank`, `share`, prompt tiếng Anh ngắn theo từng dạng | trung bình | +0.02–0.03 |
| 3 | Lấy đa số ở router và tham số tool | thấp | +0.03–0.05 (phần nhiễu còn lại) |

Đây là ước tính sơ bộ. Mỗi bước được đo theo §4 trước khi qua bước tiếp theo.

## 6. Kết quả triển khai (2026-11-12): router + task tools

Chấm bằng **đáp án thật của BTC** (`gt_submission.jsonl`). Script chấm local khớp điểm web: 0.480 ≈ 0.47, 0.557 ≈ 0.553, 0.568 ≈ 0.57.

| Bản | Điểm | Đúng | Sai | Abstain |
|---|---|---|---|---|
| Flow nhánh (nộp 0.57) | 0.568 | 663 | 95 | 242 |
| Router + task tools, temperature mặc định | 0.604 | 692 | 88 | 220 |
| Router + task tools, temperature 0 | 0.619 | 705 | 86 | 209 |
| + `unanswerable` theo manifest, tool tăng giá nhận ngày | 0.641 | 734 | 93 | 173 |
| **+ so "tháng này" theo rulebook, ưu tiên cách hiểu tháng của router** | **0.663** | **741** | **78** | 181 |

Mức trần tham khảo: Sonnet trên `data_std` được 0.783 (trả lời 783 câu, không sai câu nào).

### Thay đổi (chỉ thêm step / tool / prompt, không đổi schema)
- `task_tools.py` (mới): `compare_periods`, `best_period`, `values_by_period`, `count_where`, `rank`, `share_of`, `price_change_effect`. Mỗi tool trả `ANSWER_JSON` và finalize lấy đáp án thẳng từ đó; agent dừng ngay khi có đáp án.
- Router (`ROUTE`, `--router`): chọn dạng câu, kiểm tra scope, viết lại câu hỏi. Có thêm verdict `not_recorded` + `needs`, được kiểm tra lại bằng manifest của shop:
  - Giới tính, địa chỉ, lý do huỷ/trả, ý kiến chủ quan, lời khuyên → `unanswerable`.
  - Cột shop không ghi (nhóm hàng, nhóm khách, kênh, giờ đơn, giá vốn) → `unanswerable` chỉ khi manifest xác nhận thiếu.
  - `payment_method` và `seller` không được dùng để trả `unanswerable` (bán chịu, trả góp là dữ liệu công nợ → abstain).
- Quy ước đọc chung trong SYSTEM và trong mô tả `Period`: "T4/2026 = tháng 4"; câu không dấu; từ đệm.
- `--temperature` (dùng 0); `ANSWER_JSON` của task tool đi thẳng vào submission.

### Smoke test (20 câu các bản cũ sai ≥ 3/4 lần + 20 câu luôn đúng)
- 20 câu từng sai: net −9…−12 (4 bản cũ) → 0…+3. 20 câu luôn đúng: vẫn đúng đủ 20/20.
- Chỉ 17 câu có lỗi kỳ ("T<n>" thành quý, "tháng này"): 0 → 7 câu đúng, thêm 3 câu abstain.
- Tập `unanswerable` + tăng giá (109 câu): net 50 → 77.

Submission tốt nhất: `runs/public_test_router3_t0/submission.jsonl` (full 38 phút, 24 workers).

## 7. Từ 0.663 lên gần Sonnet: còn mất ở đâu (router3, chấm bằng đáp án thật)

| Nhóm | Số câu | Ghi chú |
|---|---|---|
| Sonnet đúng, 9B sai | 54 | mỗi câu chênh 2 điểm so với Sonnet |
| Sonnet đúng, 9B abstain | 16 | |
| Sonnet abstain, 9B trả lời sai | 24 | phần lớn do router hỏng (bên dưới) |
| Đáp án `unanswerable`, 9B được 25/29; Sonnet 8/29 | | 9B đang hơn Sonnet ở nhóm này |

Router không trả về JSON ở **77 câu (7,7%)**: thinking chạy quá dài (trung vị 261 s, các câu khác 17 s) và chạm `max_tokens`. Các câu này rơi về luồng cũ, chỉ đúng 63% khi trả lời (34 đúng / 20 sai).

Độ chính xác khi trả lời, theo dạng câu: single_value 0.95, compare 0.91, count_where 0.91, rank 0.88, share 0.88, best_period 0.94, values 0.96, price 1.00; router hỏng 0.63.

Lấy đa số trên 3 lần chạy temperature 0 (offline) chỉ +0.009, nên nhiễu không còn là đòn bẩy chính.

## 8. Router có budget thinking + verify (2026-11-12, tối ưu cho RTX 5090)

### Thời gian đi đâu (router3_t0, H100, 24 luồng)

| Node | Tổng giây | Trung vị / lần |
|---|---|---|
| analyze (router, thinking) | 39 482 (75%) | 17.6 s |
| agent (gọi tool, thinking) | 12 643 | 2.6 s |
| finalize | 249 | 0.3 s |

Node agent nghĩ ngắn (trung vị 196 token mỗi lượt, p99 1038), nên chỉ router cần tối ưu. Router nghĩ dài chủ yếu vì soát lại định dạng JSON và scope nhiều lần, không phải vì đọc câu hỏi.

### Bộ nhớ trên 5090 (32 GB)

Qwen3.5-9B chỉ có 8/32 layer full attention (24 layer linear attention), nên KV khoảng 32 KB mỗi token. Weight bf16 chiếm 16.7 GB, phần còn lại chứa được khoảng 300k token KV, đủ cho khoảng 30 request 10k token song song. Nút thắt vì vậy là **tốc độ decode**: băng thông 5090 1.8 TB/s so với 3.35 TB/s của H100, tức khoảng 0.53×. Thước đo cần tối ưu là **số token output mỗi câu**.

### Các chế độ thinking của router (`QueryContext.router_think`, `--router-think`)

- `on`: như cũ.
- `off`: tắt thinking. Kém rõ: thua 10 điểm sau 172 câu, viết lại câu sai (ví dụ "Nho xem" thành tên một nhóm hàng, "đh" thành doanh thu), và gán `needs` vô nghĩa. Đã bỏ.
- `capped`: thinking bị cắt ở `max_tokens`, rồi fallback sang tắt thinking. 23% câu chạm trần.
- **`budget`** (`_budget_reply`): nghĩ tối đa N token; nếu bị cắt thì đóng `</think>` bằng `continue_final_message` (vLLM) và cho model viết JSON từ phần đã nghĩ, không nghĩ tiếp. Giữ được phần đọc câu hỏi, chỉ bỏ phần soát lại.

Router-only, 60 câu, so với routing của router3 (bản thân `on` chạy lại chỉ trùng task 54–57/60, trùng kỳ 44–48/60, nên đây là mức nhiễu):

| Chế độ | Trùng task | Trùng kỳ | Token TB | p90 |
|---|---|---|---|---|
| on | 54 | 48 | 1078 | 2114 |
| on + dặn "nghĩ ngắn" | 57 | 44 | 2083 | 2569 |
| budget 512 | 57 | 45 | 617 | 658 |
| budget 1024 | 58 | 48 | 848 | 1157 |

### Verify (node `verify`, thinking tắt, khoảng 35 token output)

Sau khi task tool cho đáp án, một lượt LLM ngắn kiểm tra lời gọi (measure, kỳ, filter, đơn vị đếm, chiều, dạng đáp án) so với câu hỏi:
- Khớp: chốt đáp án.
- Lệch: agent gọi lại một lần, lúc này được dùng mọi task tool.
- Lệch lần hai: abstain.

Thiết kế:
- Verify đọc **câu hỏi gốc**, không đọc cách hiểu của router; nếu đọc theo router thì router hiểu sai thì verify cũng sai theo.
- Verify được mô tả của task tool.
- Không verify `price_change_effect`, vì verify nhầm "hôm nay" với ngày đổi giá (4/20 câu đúng bị gắn cờ, 0 câu sai).
- Verify áp dụng cả cho luồng information_finder (single_value). Từ final2.

### Sửa tool và prompt chung (không nhắm từng câu)

- `share_of`:
  - `cancel_rate` trả thẳng tỉ lệ huỷ (trước đây ra 100%).
  - Phần tử có giá trị 0 thì báo lỗi, vì thường là filter không tồn tại (trước đây ra 0%, sai).
- Tool mới `unsold_products` (hàng ế, theo định nghĩa trong rulebook): đúng 4/4 câu khi gọi trực tiếp.
- ROUTE:
  - Từ tương đối giữ nguyên loại tương đối (tuần rồi → last_week, ...), không tự đổi sang ngày.
  - "bao nhiêu / mấy + danh từ" là số đếm.
- Gate unanswerable: `needs` đi kèm `scope: "in"` bị bỏ qua.
- Bug: lần gọi lại sau verify từng lỗi `TypeError: unhashable type: 'StructuredTool'`, nên câu bị gắn cờ luôn thành abstain (có trong final_b512 và final_b1024). Đã sửa từ final2.

### Kết quả (1000 câu public, đáp án thật)

| Run | Điểm | ok / bad / abstain | Token router mỗi câu |
|---|---|---|---|
| router3_t0 (router on) | 0.663 | 741 / 78 / 181 | khoảng 1100–2000, đuôi tới 16k |
| final_b512 (budget 512 + verify) | 0.662 | 731 / 69 / 200 | 616, chặn trên khoảng 1.1k |
| final_b1024 (budget 1024 + verify) | 0.675 | 740 / 65 / 195 | 939, chặn trên khoảng 2k |

### Cấu hình đề xuất khi chạy trên 5090 (chưa thử trên 5090)

Chạy agent:
```
python scripts/run_agent.py --split <split> --base-url http://localhost:8000/v1 --model Qwen3.5-9B \
  --router --temperature 0 --router-think budget --think-cap 1024 --verify --workers 16
```
Nếu bị giới hạn thời gian, dùng `--think-cap 512`: điểm thấp hơn khoảng 0.013 trên public, đổi lại router bớt khoảng 1/3 token.

vLLM:
- `--max-model-len 32768`: một lời gọi dài nhất 16.5k token, p99 13k, trung vị 9.6k.
- `--enable-prefix-caching`: phần system cùng rulebook khoảng 8k token, giống nhau ở mọi lời gọi router, agent và verify của mọi câu.
- `--max-num-seqs 16–32`.
- Có thể thêm `--quantization fp8`: decode nhanh hơn trên 5090, nhưng phải đo lại điểm vì chưa thử.
- Giữ `--reasoning-parser qwen3`, vì `_budget_reply` đọc phần thinking bị cắt từ trường `reasoning` của response.

Ước lượng token output mỗi câu (b1024):
- router khoảng 940
- agent khoảng 370
- verify khoảng 35
- tổng khoảng 1.35k

Bản router3 tốn khoảng 1.5–2.4k mỗi câu, có đuôi dài tới 16k. Ở mức khoảng 90 token/s cho một luồng trên 5090, mỗi câu tốn khoảng 15 s nếu chạy tuần tự; chạy batch nhiều luồng thì nhanh hơn nhiều.
| final2 (+ verify cả luồng information_finder, sửa bug gọi lại) | 0.611* | 660 / 49 / 291 | *90 câu lỗi kết nối; trên 906 câu không lỗi kém b1024 5 điểm |

Kiểm tra overfit trên train (457 câu không lỗi kết nối ở cả hai lượt):
- final2: 379 / 10 / 68, net 369.
- Cấu hình cũ (router on, không verify): 385 / 21 / 51, net 364.

Cải tiến budget + verify vẫn có lợi trên train, và số câu sai giảm một nửa.

## 9. Vòng phân tích bằng 3 subagent (2026-11-12) và các thay đổi

Ba subagent phân tích offline (không sửa repo, không gọi LLM) ba mảng: luồng single_value, các task tool, và quyết định abstain/scope/unanswerable. Mọi đề xuất dưới đây đều đã được kiểm chứng bằng cách gọi tool offline và so với GT.

| Thay đổi | Ở đâu | Bằng chứng |
|---|---|---|
| `avg_order_value` chỉ chia cho đơn có thu tiền (rulebook: phiếu bảo hành 0 đồng không tính) | query.py `_aggregate` | 3/3 câu dev_012 khớp GT; chỉ dev_012 có đơn 0 đồng (đã kiểm tra mọi shop) |
| Filter `order_amount: paid / free` (phiếu có thu tiền / phiếu bảo hành) | query.py, tools.py `Filters` | 33, 39, 15, 2.91 khớp GT |
| Measure `day_count` (số ngày có đơn) cho khách trung thành, thợ chăm chỉ | query.py, task_tools `Measure` | 3/3 khớp GT |
| Khi hoà điểm, so tên theo chữ bỏ dấu (Đ xếp như D) | query.py `_sorted` | khớp GT "Đoàn"; không đổi đáp án nào khác trong các lời gọi đã trace |
| `rank`: `min_orders` (khách sộp ≥ 3 đơn); mô tả `by` (mặt hàng → product, nhóm → product_group) và `measure` | task_tools | khách sộp khớp GT; cả 3 lời gọi `by=product_group` trong run đều sai |
| `values_by_period(trend=...)` trả boolean cho câu "tăng liên tục ... phải không" | task_tools | GT true / false / false |
| Gắn thêm tool của họ gần: share → count_where, compare ↔ values_by_period | agent.py `_SIBLINGS` | 5 câu "khách ≥ N đơn chiếm mấy %" khớp GT với count_where |
| `find_entity` báo NO MATCH khi điểm tốt nhất < 0.5 (chỉ product / product_group) | tools.py | trong 2 run: 7 sai / 1 đúng ở các lần tra < 0.5 |
| Kỳ của router thắng kỳ agent tự tính (single_value, một lời gọi, khác ngày) | agent.py `_prefer_router_period` | 3 câu tuần này / tuần rồi khớp GT |
| Router chọn quý thì hỏi lại bằng thinking tắt: câu hỏi có nói quý / Q không | agent.py `_recheck_quarter` | 34/34 đúng (7 câu T<n> được sửa thành tháng) |
| NO DATA do tool báo thì abstain thay vì unanswerable (cổng của router vẫn giữ) | agent.py `finalize` | net âm ở cả 5 run cũ |
| Verify luồng information_finder: tách thành cờ `--verify-general`, cho thấy kết quả `find_entity` | agent.py | trong final2 lỗ khoảng −7, chủ yếu do không thấy kết quả `find_entity` |
| `run_agent`: chạy lại câu bị lỗi kết nối; nhận nhiều `--base-url` và chia xoay vòng | scripts/run_agent.py | final2 và train bị khoảng 90 câu lỗi do port-forward rớt |

Chưa làm vì cần đổi prompt router và có rủi ro:
- Siết `needs="reason"` (20 đúng / 13 sai) và `product_group` (23 / 9).
- Thêm mục scope trong VERIFY.
- Tool riêng cho "dòng máy" / "hãng" của shop điện thoại.

### Kết quả final3 (1000 câu public, đáp án thật; chạy trên 3 replica, không có lỗi kết nối)

| Run | Điểm | ok / bad / abstain |
|---|---|---|
| router3_t0 | 0.663 | 741 / 78 / 181 |
| final_b1024 | 0.675 | 740 / 65 / 195 |
| **final3** (verify chỉ cho task tool) | **0.700** | 755 / 55 / 190 |
| **final3_vg** (thêm `--verify-general`) | **0.706** | 743 / 37 / 220 |

Train (đáp án của ban tổ chức):
- final3 trên đủ 591 câu: 494 / 25 / 72, điểm 0.794.
- So trên 457 câu không lỗi kết nối ở mọi lượt:

| Bản | ok / bad / abstain | net |
|---|---|---|
| final3 | 389 / 17 / 51 | 372 |
| final2 | 379 / 10 / 68 | 369 |
| Cấu hình cũ (router on, không verify) | 385 / 21 / 51 | 364 |

Chi phí final3 trên public (H100):
- Token output khoảng 1.2k mỗi câu: router 832, agent 355, verify 36.
- Thời gian trung vị 16.6 s mỗi câu, p90 27 s. router3 trung vị 24 s, đuôi tới vài phút.
