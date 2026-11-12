# Khảo sát dữ liệu ngoài `sales_lines` và đề xuất bảng mới cho data_std

Ngày: 2026-11-12

## Tóm tắt

- Hiện data_std chỉ có một bảng `sales_lines` (mỗi dòng là một dòng hàng của đơn bán). Agent hiện tại (Qwen3.5-9B, bản final3_vg) đạt **0.706** trên 1000 câu public, chấm bằng đáp án thật.
- Còn **220 câu bỏ qua**. Khoảng **185 câu** trong số đó bỏ qua vì dữ liệu cần dùng nằm ở các file chưa được chuyển đổi: công nợ, nhập hàng, tồn kho, lô thuốc, danh mục thuốc, sổ sửa xe, sổ trả góp…
- Nếu trả lời đúng khoảng 65% số câu này, điểm có thể tăng thêm khoảng **+0.10 đến +0.12**.
- Đề xuất **6 bảng mới, tách riêng khỏi `sales_lines`**. Bảng cũ và schema của nó giữ nguyên. Có thể làm theo 3 đợt; riêng đợt 1 chỉ cần 3 bảng và gỡ được khoảng 95 câu.

## 1. Các loại file trong dữ liệu (30 shop train)

| Loại file | Một dòng là | Shop có |
|---|---|---|
| sales_lines | một dòng hàng của đơn | 29/30 (đã chuyển đổi) |
| orders | một đơn: tổng sau giảm, đã trả, nợ, kênh, hình thức TT | file riêng ở 12 shop; các shop còn lại lặp trên dòng hàng |
| products | mã, tên, nhóm, ĐVT, giá, giá vốn | 21 shop |
| customers | mã, tên, SĐT, nhóm | 11 shop |
| payments | một lần khách trả tiền cho một đơn | 7 shop |
| imports | phiếu nhập NCC (đầu phiếu và dòng) | 12 shop |
| stock | tồn cuối kỳ theo đơn vị cơ bản | 9 shop (123 có sổ kho dạng ledger) |
| price_history | lịch sử đổi giá | 4 shop |
| lots | lô, hạn dùng, số lượng bán/trả theo lô | 6 shop (nhà thuốc) |
| drugs | hoạt chất, thuốc kê đơn | 6 shop (nhà thuốc) |
| deliveries / debt_terms | đợt giao, hạn trả theo đơn | 4 shop |
| loại riêng lẻ | chi phí, bàn/khu vực, nhân viên, trả hàng | 1–2 shop mỗi loại |

## 2. File chưa được dùng ở 14 shop public

| Shop | Ngành | File/bảng thô chưa dùng | Nội dung |
|---|---|---|---|
| dev_003 | cà phê | `BanHang_PhieuThu`, `SoTay_NhapHang`, `SoTay_ChiPhi`, `ton_kho.csv`, `BaoCaoCa` | phiếu thu nợ, nhập hàng, chi phí tháng, tồn kho, tiền mặt theo ca |
| dev_004 | VLXD | `SoHanNo`, `SoGiaoHang`, `SoNhapHang`, `TonKho`, `BangGia` | hạn trả nợ, đợt giao, nhập hàng, tồn, bảng giá |
| dev_005 | nhà thuốc | `DanhSachThuoc`, `DonNhapHang`, `DonNhapHang_ChiTiet` | hoạt chất, kê đơn, phiếu nhập kèm lô và hạn dùng |
| dev_006 | nhà thuốc (sqlite) | `thanh_toan`, `phieu_nhap`, `chi_tiet_nhap`, `lo_hang`, `chi_tiet_don_lo`, `the_kho`, `thong_tin_thuoc`, `khach_hang`, `nha_cung_cap` | thanh toán, nhập, lô, sổ kho, thuốc, khách |
| dev_007 | nhà hàng | `chi_tiet_nhap_hang.csv`, cột tồn/giá vốn trong danh mục | nhập hàng, giá trị tồn |
| dev_008 | nhà hàng | `ChiTietNhapHang`, cột Note nợ, Tồn thực | nhập hàng, nợ còn lại, tồn |
| dev_010 | mẹ và bé | `thanh_toan.csv`, cột Còn nợ | thanh toán, nợ |
| dev_011 | điện thoại | cột Thu máy cũ, Khách trả | thu máy cũ, khách phải trả |
| dev_012 | sửa xe | `DanhSachTho` (% hoa hồng); cột Làm gì, Ghi chú của `SoSuaXe` | hoa hồng, loại việc, dòng xe, phiếu bảo hành |
| dev_013 | mẹ và bé | `QuanLy[DanhMuc, KhachHang, NhapHang, KhuyenMai]`; dòng voucher/điểm/ship | hãng, size, ngày đăng ký thành viên, nhập hàng, voucher/ship |
| dev_014 | điện thoại | `SoNo[Sổ nợ, Trả góp, Bảo hành]`, `ThuMayCu` | nợ, trả góp, bảo hành, thu máy cũ |
| dev_001, 002, 009 | — | không có file thêm | — |

## 3. Câu hỏi đang bỏ qua theo nhóm (public, bản final3_vg)

| Nhóm câu hỏi | Số câu (ước) | Shop | Ví dụ câu hỏi |
|---|---|---|---|
| Công nợ, trả nợ, tuổi nợ, trả góp, hạn trả | ~60 | 003, 004, 006, 008, 010, 014 | "Top 3 khách nợ nhiều nhất đến hết 12/06/2026"; "nợ chia theo tuổi 0–7, 8–15, trên 15 ngày" |
| Thuộc tính hàng: kê đơn, hoạt chất, hãng, size, dòng máy | ~35 | 005, 006, 013, 011, 014 | "Th5/2025 thuốc kê đơn chiếm bao nhiêu % doanh thu?"; "bỉm size XL mọi hãng bán mấy bịch?" |
| Nhập hàng, NCC, tồn kho, lô, hạn dùng, nhập trừ bán | ~33 | 003–008, 013 | "Tháng 8/2026 quán cất hàng hết bao nhiêu tiền?"; "lô gần hết hạn nhất của Augmentin" |
| Tiền cấp đơn: khách phải trả, thu máy cũ, voucher, điểm, phí ship | ~25 | 011, 013, 014 | "Th6/2026 voucher giảm cho khách hết bao nhiêu?" |
| Sửa xe: hoa hồng, việc làm, dòng xe, bảo hành | ~15 | 012 | "Bảng hoa hồng thợ th7/2026" |
| Bảo hành máy | ~7 | 014 | "Quý 3/2026 lượt bảo hành so với máy bán chiếm bao nhiêu %?" |
| Đợt giao hàng | ~7 | 004 | "Đơn đặt tháng 11/2025 có bao nhiêu đơn giao từ 2 đợt?" |
| Chi phí, lãi ròng | ~4 | 003 | "Tháng 7/26 trừ chi phí quán còn lãi ròng bao nhiêu?" |
| **Tổng** | **~185** | | |

Khoảng 35 câu còn lại bị bỏ qua vì lý do khác (agent hiểu sai câu hỏi, không tìm được mặt hàng…). Các bảng mới không giải quyết được nhóm này.

## 4. Đề xuất: 6 bảng mới

Nguyên tắc chung:
- **Không gộp vào bảng hiện có.** Mỗi bảng là một file `data_std/<shop>/<bảng>.parquet`. File `sales_lines.parquet` và `manifest.json` giữ nguyên.
- **Mỗi shop có thêm một file `tables.json`.** File này ghi bảng nào có, cột nào trống và khoảng ngày của dữ liệu. Câu hỏi về dữ liệu shop không ghi thì trả `unanswerable`, không trả 0.
- **Gộp dữ liệu theo dạng sự kiện thay vì mỗi file một bảng.** Ví dụ nhập hàng, lô và tồn kho đều là biến động kho nên chung một bảng; sửa xe, bảo hành, giao hàng và chi phí là sự kiện dịch vụ nên chung một bảng. Nhờ vậy giữ được ở 6 bảng.

| # | Bảng | Một dòng là | Cột chính | Gộp từ |
|---|---|---|---|---|
| 1 | `products` | một mặt hàng | product_key, name, group, brand, size_label, model_line, active_ingredient, is_prescription, catalog_cost, price, made_in_house | danh mục hàng, danh mục thuốc, `QuanLy[DanhMuc]`, sheet Ghi tắt |
| 2 | `customers` | một khách | customer_key, name, phone, group, gender, register_date, points | danh sách khách |
| 3 | `orders` | một đơn | order_id, order_date, customer_key, status, channel, payment_method, is_credit, goods_amount, order_discount, shipping_fee, voucher_amount, points_amount, trade_in_amount, total_payable, due_date | đầu đơn, cột tổng/nợ lặp trên dòng hàng, `SoHanNo`, `ThuMayCu`, dòng voucher/ship |
| 4 | `debt_events` | một biến động nợ | event_id, kind (payment / instalment_due), order_id, customer_key, date, amount, method, period_no | phiếu thu, `thanh_toan`, `SoNo[Sổ nợ, Trả góp]` |
| 5 | `stock_moves` | một biến động kho | kind (import / sale_lot / return_lot / snapshot), date, doc_id, supplier, product_key, lot_id, expiry_date, qty_base, unit_cost_base, amount, status | phiếu nhập, lô, sổ kho, file tồn kho |
| 6 | `service_events` | một sự kiện dịch vụ | kind (repair / warranty / delivery / expense), date, ref_id, party (thợ, lái xe, khách), item (dòng xe, máy, hàng), jobs, qty, amount, is_warranty, category | `SoSuaXe`, `DanhSachTho`, `SoNo[Bảo hành]`, `SoGiaoHang`, `SoTay_ChiPhi` |

Các quy tắc trong rulebook được viết thành code trong tool, model không phải nhớ. Ví dụ:
- Tuổi nợ tính từ ngày đặt đơn.
- Đơn COD và đơn bán qua sàn TMĐT không tính là khách nợ.
- Tiền vốn của lô sắp hết hạn tính theo giá nhập của chính lô đó.
- Phiếu bảo hành không tính vào tiền bình quân mỗi phiếu.

## 5. Tool cho agent

| Tool | Đọc bảng | Trả lời được |
|---|---|---|
| `information_finder` (đã có, mở rộng) | `sales_lines` nối với `products`, `customers` | lọc và nhóm theo kê đơn, hoạt chất, hãng, size, dòng máy; khách thành viên mới |
| `debt_finder` (mới) | `orders` + `debt_events` | nợ tại ngày X, top khách nợ, tuổi nợ, nợ thêm và trả nợ trong kỳ, hạn trả, kỳ góp quá hạn |
| `order_finder` (mới) | `orders` | khách phải trả, thu máy cũ, voucher, điểm, phí ship |
| `inventory_finder` (mới) | `stock_moves` + `products` | tiền nhập, NCC chính, nhập trừ bán, giá trị tồn, mặt hàng hết hàng, lô sắp hết hạn |
| `service_finder` (mới) | `service_events` | lượt sửa theo việc, dòng xe, thợ; hoa hồng; bảo hành; đợt giao; chi phí và lãi ròng |

Router chỉ gắn đúng một tool theo nhóm câu hỏi. Prompt vì vậy không dài thêm, phù hợp với giới hạn thời gian chạy trên RTX 5090.

## 6. Lộ trình

| Đợt | Bảng | Số câu gỡ được (ước) | Ghi chú |
|---|---|---|---|
| 1 | `products`, `orders`, `debt_events` | ~95 (thuộc tính hàng ~35, công nợ và trả góp ~60) | lợi nhất; công nợ có test đối chiếu bằng pandas |
| 2 | `stock_moves` | ~33 | nhập hàng, tồn, lô |
| 3 | `customers`, `service_events`, phần tiền cấp đơn còn lại của `orders` | ~55 | mỗi loại chỉ có ở 1–2 shop |

Mỗi đợt kiểm tra hai bước. Trước hết so các câu liên quan với đáp án public. Sau đó chạy lại train (5 shop đã chuyển đổi) để chắc các câu đang đúng không bị tụt.

## 7. Rủi ro và điểm cần quyết

1. **Test ẩn có shop mới.** Converter viết tay chỉ dùng được cho public và train. Ở test ẩn, các bảng mới chỉ có ích khi analyst_agent (bước tự chuyển đổi shop lạ) sinh được chúng. Đề xuất làm converter tay trước để chốt schema và tool, sau đó mở rộng analyst_agent theo đúng 6 bảng này.
2. **Dữ liệu nợ không đồng nhất giữa các shop.**
   - Có shop ghi từng lần trả (003, 006, 010).
   - Có shop chỉ ghi tình trạng nợ ở ngày cuối (008). Shop này không trả lời được câu hỏi nợ tại một ngày trong quá khứ.
   - Có shop ghi sổ tay tự do (014).
   Bảng `debt_events` phải chấp nhận việc thiếu ngày trả; khi đó tool trả `unanswerable` thay vì đoán.
3. **Chuẩn hóa văn bản tự do.** Gồm dòng xe, loại việc sửa và tên máy. Không viết từ điển cứng; cần một bước LLM chạy offline lúc chuyển đổi.
4. **Thời gian chạy.** Thêm tool không làm tăng số token sinh ra mỗi câu vì router chỉ gắn một tool. Các câu công nợ trước đây bị bỏ qua sớm, nay sẽ phải gọi tool, nên mỗi câu loại này chạy thêm khoảng 1 lượt tool.
