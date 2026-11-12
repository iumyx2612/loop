"""Checks 1+2: which formula links qty, unit price, line discount and line amount, per shop."""
import sys
from collections import Counter
sys.path.insert(0, sys.argv[1])
from lines_lib import load, money, qty
sys.stdout.reconfigure(encoding="utf-8")

# shop: ([(file, sheet, header_row)], qty, price, discount or None, amount)
CFG = {
    "biz_101": ([("ban_hang.csv", None, 1)], "so_luong", "Giá bán", None, "Thanh tien"),
    "biz_102": ([("DonHang_ChiTiet.xlsx", None, 1)], "Số lượng sản phẩm", "Giá sản phẩm", None, "Tổng tiền"),
    "biz_103": ([("DonHang_ChiTiet.xlsx", None, 3)], "Số lượng sản phẩm", "Giá sản phẩm", None, "Tổng tiền"),
    "biz_104": ([("DonHang_ChiTiet.xlsx", None, 1)], "Số lượng sản phẩm", "Giá sản phẩm", None, "Tổng tiền"),
    "biz_105": ([("SoBanHang_1.xlsx", None, 2), ("SoBanHang_2026.xlsx", None, 2)], "so_luong", "Giá lẻ", None, "Trị giá"),
    "biz_106": ([("DanhSachHoaDonChiTiet_1.xlsx", None, 3), ("DanhSachHoaDonChiTiet_2.xlsx", None, 3)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_107": ([("SoBanHang.xlsx", None, 1)], "Số lượng", "don_gia", None, "Tổng tiền"),
    "biz_108": ([("DanhSachHoaDonChiTiet.xlsx", None, 1)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_109": ([("DanhSachHoaDonChiTiet.xlsx", None, 1)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_111": ([("chi_tiet_don.csv", None, 1)], "SL", "Giá đơn vị", None, "T.Tiền"),
    "biz_112": ([("So_chi_tiet_ban_hang_1.xlsx", None, 4), ("So_chi_tiet_ban_hang_2026.xlsx", None, 4)], "Số lượng", "Đơn giá", None, "Thành tiền"),
    "biz_113": ([("SoBanHang.xlsx", None, 1)], "SL bán", "Đơn giá bán", None, "thanh_tien"),
    "biz_114": ([("ban_hang.csv", None, 1)], "Sluong", "Giá bán lẻ", None, "Tổng"),
    "biz_115": ([("So_chi_tiet_ban_hang.xlsx", None, 1)], "Số lượng", "Đơn giá", None, "Thành tiền"),
    "biz_116": ([("Bao_cao_ban_hang.xlsx", "So_chi_tiet_ban_hang", 1)], "Số lượng", "Đơn giá", None, "Thành tiền"),
    "biz_117": ([("SoBanHang.xlsx", None, 2)], "Số lượng hàng", "Giá", None, "Tiền"),
    "biz_118": ([("So_chi_tiet_ban_hang.xlsx", None, 4)], "Số lượng", "Đơn giá", None, "Thành tiền"),
    "biz_119": ([("DanhSachHoaDonChiTiet.xlsx", None, 1)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_120": ([("DanhSachHoaDonChiTiet.xlsx", None, 1)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_121": ([("DonHang_ChiTiet.xlsx", None, 3)], "Số lượng sản phẩm", "Giá sản phẩm", None, "Tổng tiền"),
    "biz_122": ([("DonHang_ChiTiet_1.xlsx", None, 3), ("DonHang_ChiTiet_2026.xlsx", None, 3)], "Số lượng sản phẩm", "Giá sản phẩm", None, "Tổng tiền"),
    "biz_123": ([("pos.sqlite", "chi_tiet_don", 1)], "SL", "GiaDonVi", "ChietKhau", "TriGia"),
    "biz_124": ([("BanHang_ChiTietHoaDon.xlsx", None, 1)], "Slg", "Giá niêm yết", "Khuyến mãi", "Thanh tien"),
    "biz_125": ([("ban_hang.csv", None, 1)], "SL bán", "Giá 1 đv", None, "Amount"),
    "biz_126": ([("SoBanHang.xlsx", None, 1)], "Số lượng bán", "Price", None, "Thành tiền (đ)"),
    "biz_127": ([("chi_tiet_don.csv", None, 1)], "SoLuong", "Đơn giá (đ)", None, "Amount"),
    "biz_128": ([("DanhSachHoaDonChiTiet.xlsx", None, 1)], "Số lượng", "Đơn giá", "Giảm giá", "Thành tiền"),
    "biz_129": ([("chi_tiet_don.csv", None, 1)], "S.L", "Giá 1 đv", "Giảm giá", "Tiền bán"),
    "biz_130": ([("So_chi_tiet_ban_hang_1.xlsx", None, 4), ("So_chi_tiet_ban_hang_2.xlsx", None, 4)], "Số lượng", "Đơn giá", None, "Thành tiền"),
}

for shop, (files, qc, pc, dc, ac) in CFG.items():
    if len(sys.argv) > 2 and shop not in sys.argv[2:]:
        continue
    rows = [r for f, sh, hr in files for r in load(shop, f, sh, hr)]
    hits, bad, examples = Counter(), Counter(), []
    n = 0
    for r in rows:
        try:
            q, p, a = qty(r.get(qc)), money(r.get(pc)), money(r.get(ac))
            d = money(r.get(dc)) if dc else 0.0
        except ValueError as e:
            bad[str(e)[:50]] += 1
            continue
        if q is None or p is None or a is None:
            bad["missing qty/price/amount"] += 1
            continue
        d = d or 0.0
        n += 1
        ok = []
        if abs(q * p - a) < 1:
            ok.append("q*p")
        if dc and d and abs(q * p - d - a) < 1:
            ok.append("q*p-d")
        if dc and d and abs(q * (p - d) - a) < 1:
            ok.append("q*(p-d)")
        key = "+".join(ok) if ok else "none"
        if dc:
            key += " (d>0)" if d else " (d=0)"
        hits[key] += 1
        if not ok and len(examples) < 4:
            examples.append({k: r.get(k) for k in (qc, pc, dc, ac) if k} | {"_row": f"{r['_sheet']}:{r['_row']}"})
    print(f"## {shop}  lines={n}  " + ", ".join(f"{k}: {v}" for k, v in hits.most_common()))
    if bad:
        print("   unparsed:", bad.most_common(4))
    for e in examples:
        print("   no formula:", e)
