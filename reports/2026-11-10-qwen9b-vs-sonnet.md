# Report: Qwen3.5-9B vs Sonnet trên public_test — vì sao chỉ 0.47 — 2026-11-10

## 1. Tóm tắt

- Bản 9B (`runs/public_test_qwen9b_final/submission.jsonl`) nộp được **0.47**.
- So từng câu với bản Sonnet trên cùng `data_std` + cùng agent (`dataset/zaloAI/gt-sonnet/`), dùng `src/datamate/grading.py`:

| Nhóm | Số câu | Ý nghĩa nếu coi Sonnet là đúng |
|---|---|---|
| Trùng đáp án | 630 | +1 |
| Cả hai abstain | 156 | 0 |
| **Lệch đáp án** | **117** | 9B −1 |
| **Sonnet abstain (OUT OF SCOPE), 9B vẫn trả lời** | **61** | 9B −1 (gần như chắc chắn sai) |
| 9B abstain, Sonnet trả lời | 36 | 9B mất +1 |

- Điểm ước tính theo proxy: (630 − 117 − 61) / 1000 = **0.45 ≈ 0.47 thực tế** → dùng bản Sonnet làm proxy để chẩn đoán là hợp lý.
- Lỗi của 9B **không nằm ở tool/data** (cùng data_std, cùng tool, Sonnet làm được), mà ở **hiểu câu hỏi**: đọc sai kỳ thời gian, sai measure, không giữ kỷ luật OUT OF SCOPE, sai format date.
- Trần nếu sửa hết các lỗi 9B (đạt mức Sonnet): ~0.45 + 0.117 + 0.061 + 0.036 ≈ **+0.2** — xem ưu tiên ở §4.

## 2. Phân loại 117 câu lệch đáp án

Tự động so tool call của 9B với Sonnet (measure, khoảng ngày tool thực sự tính, group_by, filters):

| Nguyên nhân | Số câu | Ví dụ (câu hỏi → Sonnet / 9B) |
|---|---|---|
| **D. Sai kỳ thời gian** | **46** | "T1/2025 bán được bao nhiêu tiền" → `month 1/2025` / **`quarter 1`** · "Máy cũ **8/25**" → `month 8/2025` / **ngày 25/08/2026** · "7/25 nhóm nước ép…" → `month 7/2025` / **range 03/2025→08/2026** · "Tuần nay…" → `this_week` / **`today`** · "Thang nay…" → `this_month` / **`today`** · "th này" → `this_month` / **`all_before`** · "10/25 ca chiều…" → `month 10/2025` / **`this_month`** |
| H. Cùng tool call, khác bước tính / chọn kết quả | 16 | Tết vs 17 ngày trước Tết: −16.02 / **−843** (pct_change sai) · "cùng kỳ tháng trước" không dùng `compare="previous"` |
| A. Sai format date (trả string) | 15 | "tháng nào bán nhiều nhất" → `"2025-05"` / **`"5/25"`**, **`"Tháng 3/2026"`**; nhiều câu đúng giá trị nhưng type `string` thay vì `date` |
| B. Sai answer type | 14 | trả `string`/`list` cho câu `number`; trả `percent` khi Sonnet `unanswerable` |
| E. Sai group_by | 11 | "bao nhiêu **ngày** doanh thu ≥ 40tr" thiếu `group_by="day"` → 0 · "% khách mua ≥ 4 đơn" thiếu `group_by="customer"` · "thợ nhiệt tình nhất" |
| C. Sai measure | 10 | "bao nhiêu **đh** / **o đờ**" → `order_count` / **`revenue`** (ra 2.568.866.500 "đơn") · "bao nhiêu **máy**" → `qty_base` / `revenue` · "tỉ lệ đơn huỷ" → `cancel_rate` / `order_count` |
| F. Sai filter / entity | 4 | "dòng máy" lấy theo nhóm "phụ kiện / máy mới / máy cũ" |
| G. Làm tròn | 1 | 62.80487 / 62.8 |

→ **Sai kỳ + sai measure + sai format/type ≈ 85/117 (73%)** đều là lỗi *đọc câu hỏi*, xử lý được bằng code xác định (không cần model to hơn).

## 3. Các nhóm abstain

### 3.1 61 câu 9B trả lời khi lẽ ra phải OUT OF SCOPE (−1 mỗi câu)
60/61 là Sonnet trả `OUT OF SCOPE`. 9B không giữ luật scope trong prompt, cố trả lời từ bảng bán hàng:

| Câu hỏi | 9B trả |
|---|---|
| "T4/2026 sp nào **lãi gộp** nhiều nhất?" (shop không có `cost_price`) | "Cà phê sữa (M)" |
| "Th5/2025 **thuốc kê đơn** chiếm bao nhiêu %" | 2.67 |
| "**Tổng tiền thu máy cũ** của khách 02/2026" | 334.900.000 |
| "hàng **ko kê đơn** bán được nhiêu tiền" | 277.089.300 |
| "các **hợp đồng trả góp** còn lại bao nhiêu tiền" | unanswerable (không có NO DATA thật → vẫn sai type) |
| "mấy đơn **bán chịu** ghi ngày phải trả…" | unanswerable |

Đây là nhóm **lợi nhất để sửa**: chỉ cần abstain đúng là +61 điểm-câu (từ −1 lên 0) mà không cần trả lời đúng gì thêm.

### 3.2 36 câu 9B abstain, Sonnet trả lời
- 20 recursion limit (cùng nguyên nhân §2-D: đọc sai kỳ / viết tắt → loay hoay; chi tiết trong report trước, §5.1)
- 11 finalize abstain, 5 tự cho OUT OF SCOPE sai.

## 4. Cần cải thiện gì (ưu tiên theo điểm thu được / công sức)

Ràng buộc giữ nguyên: không đổi schema; chỉ thêm step / tool / prompt.

| # | Thay đổi | Nhóm xử lý | Câu ảnh hưởng (ước tính) | Loại |
|---|---|---|---|---|
| 1 | **Step "scope gate" trước agent** (code): từ khoá OUT OF SCOPE của prompt (lãi gộp khi shop thiếu `cost_price`, kê đơn / không kê đơn, hoạt chất, trả góp, bán chịu / công nợ / nợ, nhập hàng, tồn kho, thu máy cũ, bảo hành…; có xử lý không dấu: "no them", "lai gop", "ke don") + `manifest.columns_missing` → trả OUT OF SCOPE ngay, không gọi LLM | §3.1 | ~60 câu −1 → 0 | code |
| 2 | **Step chuẩn hoá câu hỏi + parse kỳ** (code): "T1/2025", "th3/2026", "8/25", "1-2026", "quý 4/2025", "tuần nay / tuần rồi", "tháng này / th này / thang nay", "hôm qua", "N ngày gần nhất", "Tết 2026"… → `Period` đúng schema, đưa vào USER message dạng *"Kỳ đã parse: {...}. Dùng đúng kỳ này."*; bung viết tắt (dthu, đh, o đờ, bnhiu, bi huy…) | §2-D, §3.2 | 46 + ~20 recursion | code + prompt |
| 3 | **Gợi ý measure theo từ khoá** trong cùng step: đơn / đh / o đờ → `order_count`; máy / chiếc / gói / lon / ly → `qty_base`; tỉ lệ huỷ → `cancel_rate`; bình quân 1 đơn → `avg_order_value` | §2-C | 10 | code + prompt |
| 4 | **Hậu xử lý finalize** (code): câu "tháng nào" / "ngày nào" → ép type `date` và chuẩn hoá "5/25", "Tháng 3/2026", "03/2026" → `YYYY-MM` / `YYYY-MM-DD`; câu "bao nhiêu %" → `percent` | §2-A, một phần B | 15 + vài câu | code |
| 5 | **Recipe theo dạng câu cho 9B** (prompt few-shot ngắn, chỉ nhánh self-hosted): "bao nhiêu ngày ≥ X" → `group_by="day"` + `count(r1.value >= X)`; "% khách mua ≥ N đơn"; "so với cùng kỳ / tháng trước" → `compare="previous"`; Tết vs trước Tết. Prompt hiện đã có các recipe này nhưng 9B bỏ qua → đưa recipe khớp dạng câu vào USER message thay vì để trong SYSTEM dài | §2-E, H | ~27 | prompt |
| 6 | Lỗi `calculate` kèm danh sách hàm đúng (`top(r1.name, 3)`, `first(r1.name)`…) | recursion | vài câu | tool |
| 7 | (Cân nhắc) ép finalize khi sắp chạm recursion limit — chỉ sau khi có 1–3, vì dễ tăng −1 | §3.2 | ? | step |

Ước tính: làm **1 + 2 + 3 + 4** (đều là code xác định, ít rủi ro) xử lý ~150 câu đang mất điểm → kỳ vọng **0.47 → ~0.6**. Mục 5 cần đo thêm. Với phần còn lại (H, E, chọn kết quả), 9B có thể chạm trần; khi đó cân nhắc model lớn hơn (Qwen3.5-35B-A3B) chỉ cho agent node.

### 4.1 Hướng đã chốt (thay cho bảng trên): không fix cứng, thêm step "analyze" bằng LLM

Không dùng regex, từ điển viết tắt hay danh sách từ khoá cứng. Thay vào đó thêm một node LLM trước agent:

```
START → analyze → (scope = out) → finalize (OUT OF SCOPE)
               ↘ (scope = in)  → agent ⇄ tools → finalize
```

**Node `analyze`** (prompt riêng, ngắn, thinking bật). Input: câu hỏi, ngày tham chiếu, `manifest` (các cột có / thiếu, khoảng ngày của data), danh sách measure, group_by và filter hợp lệ. Output là JSON text (chỉ là state nội bộ, không đụng schema Submission):
- `clarified_question`: viết lại câu cho rõ (bung viết tắt, thêm dấu, kỳ viết đầy đủ). Model tự suy ra, không có bảng tra.
- `period`: kỳ theo đúng format `Period` của tool.
- `measure`, `group_by`, `filters`, `steps` (cần `compare` hay `calculate` gì).
- `scope`: `in` / `out`, kèm lý do là cột nào thiếu trong manifest.
- `answer_type` và `format` (ví dụ `date`, `YYYY-MM`).

**Cách dùng output:**
- Agent nhận `clarified_question` và kế hoạch trong USER message, coi là gợi ý; vẫn được sửa nếu tool trả về điều khác.
- `scope = out` → đi thẳng finalize, trả OUT OF SCOPE (§3.1, 61 câu).
- Finalize nhận `answer_type` / `format` để ra đúng type và format date (§2-A, B).

**Tool phụ (tuỳ chọn) `resolve_period`**: nhận `Period` có cấu trúc, trả về khoảng ngày thật ("2025-08-01 → 2025-08-31"). Analyze/agent dùng để tự kiểm tra cách hiểu kỳ. Tool chỉ tính lịch, không parse chữ.

Nhóm lỗi xử lý được: D (kỳ), C (measure), E (group_by), A/B (type/format), §3.1 (scope), phần lớn recursion. Rủi ro: 9B có thể hiểu sai ngay ở bước analyze → đo trên train có đáp án và trên proxy Sonnet trước khi nộp. Chi phí: thêm khoảng 1 lượt gọi LLM mỗi câu.

## 5. Cách đo sau mỗi thay đổi

- Script so sánh (proxy Sonnet), đã dùng cho report này: phân loại lệch theo measure / kỳ / group_by / filter / type.
- Train có đáp án thật: chạy ~100 câu train (5 shop đã convert) để đo precision, tránh overfit theo Sonnet (Sonnet cũng có câu sai).
- Mục tiêu theo dõi: số câu nhóm D, C, A và số câu "trả lời khi lẽ ra OUT OF SCOPE" phải về gần 0.

## 6. Phương pháp

- Ghép theo `id`; "trùng" = `is_correct(9B, {type, answer của Sonnet, alias: []})` (không có alias nên số trùng là cận dưới, vd list tên sản phẩm đúng mà khác mã).
- Phân loại theo thứ tự ưu tiên: date→string, khác type, không chung measure, khoảng ngày tool tính không giao nhau (lấy từ output "A → B" của `information_finder`), khác group_by, khác filters, chênh làm tròn, còn lại = khác bước tính.
- Dữ liệu: `dataset/zaloAI/gt-sonnet/{submission,traces}.jsonl`, `runs/public_test_qwen9b_final/{submission,traces}.jsonl`.

## 7. Kết quả sau khi thêm step `analyze` (2026-11-10)

Code: node `analyze` trong `agent.py` (START → analyze → agent/finalize), prompt `ANALYZE`/`ANALYSIS` trong `prompts.py`, state thêm `analysis`, cờ `--no-analyze` trong `run_agent.py`; `RECURSION_LIMIT` 14 → 15 (thêm 1 bước). Không đổi schema Submission/tool.

| Bản | Trùng | Lệch | Trả lời khi Sonnet OOS | 9B abstain, Sonnet trả lời | Proxy |
|---|---|---|---|---|---|
| Cũ (nộp 0.47) | 630 | 117 | 61 | 36 | 0.452 |
| analyze v1 | 629 | 84 | 30 | 70 | 0.515 |
| **analyze v2** (scope chỉ "out" với loại dữ liệu ngoài bảng bán hàng; thiếu cột → "in" để tool trả NO DATA) | **644** | **85** | **26** | **54** | **0.533** |

- Train 100 câu (có đáp án thật, seed 1): không analyze 0.66 → analyze v1 0.69.
- Thời gian: ~24–27 s/câu (gấp ~2.5 lần do thêm 1 lượt thinking), full public_test ~27 phút với 16 workers.
- Còn lại: 31 recursion limit, 54 abstain mà Sonnet trả lời, 85 câu lệch → cần trace tiếp.
- Submission: `runs/public_test_analyze2/submission.jsonl`.

## 8. Thử nghiệm tiếp theo sau analyze v2 (2026-11-11)

| Bản | Thay đổi | Trùng | Lệch | Trả lời khi Sonnet OOS | Abstain khi Sonnet trả lời | Proxy |
|---|---|---|---|---|---|---|
| analyze v2 (nộp 0.553) | — | 644 | 85 | 26 | 54 | 0.533 |
| v3 | prompt analyze thêm các loại kỳ và danh sách measure/group_by; agent bám theo plan; khi model dừng mà không viết kết luận thì kết luận gồm mọi output của tool (trước đây chỉ output cuối); finalize gọi lại LLM khi type khác với type analyze dự đoán | 647 | 85 | 28 | 51 | 0.534 |
| v4 | v3 + định nghĩa answer_type trong prompt analyze (+ chạy lại 18 câu lỗi kết nối) | 645 | 87 | 29 | 51 | 0.529 |

- Chạy riêng 85 câu đang lệch thì v3 sửa được 37 câu, nhưng trên full các câu khác lại sai mới với số lượng tương đương. Kết quả: không cải thiện.
- Sai type date giảm từ 15 → 9 (v4); bù lại nhóm "khác bước tính" tăng 15 → 20, sai kỳ 28 → 19–21.
- Độ dao động giữa hai lần chạy cùng code khoảng ±0,01, nên v2, v3 và v4 coi như ngang nhau.
- Kết luận: chỉnh prompt cho 9B đã gần chạm trần (~0,53 proxy / ~0,55 thật). Muốn tăng tiếp cần đòn bẩy khác (xem trả lời trong chat).

## 9. Flow nhánh: insight → tối đa 3 cách hiểu chạy song song → synthesize (2026-11-11)

Code: `--branches 3` (mặc định); node `analyze` (chế độ insight, prompt `INSIGHT`) → `branches` (mỗi cách hiểu chạy subgraph agent ⇄ tools riêng, song song) → `synthesize` (prompt `SYNTHESIZE`: chọn cách hiểu khớp câu hỏi, 0 = abstain) → `finalize`. `--branches 0` quay về analyze đơn.

| Bản | Trùng | Lệch | Trả lời khi Sonnet OOS | Abstain khi Sonnet trả lời | Proxy |
|---|---|---|---|---|---|
| v2 (nộp 0.553) | 644 | 85 | 26 | 54 | 0.533 |
| **Nhánh (≤3)** | **653** | **80** | **25** | **50** | **0.548** |

- Số nhánh mở: 1 nhánh 778 câu, 2 nhánh 47, 3 nhánh 4, 171 câu OUT OF SCOPE từ insight; synthesize abstain 29 câu.
- Thời gian full: 27,4 phút (16 workers), ~26 s/câu — ngang bản analyze đơn vì hầu hết chỉ 1 nhánh.
- +0,015 proxy so với v2, nằm sát mức dao động giữa các lần chạy (±0,01).
- Submission: `runs/public_test_branch3/submission.jsonl`.
