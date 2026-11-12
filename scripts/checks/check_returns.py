import sys
from collections import Counter, defaultdict
sys.path.insert(0, sys.argv[1])
from lines_lib import load, money, qty, day
sys.stdout.reconfigure(encoding="utf-8")
CFG = {"biz_105": ([("SoBanHang_1.xlsx", 2), ("SoBanHang_2026.xlsx", 2)], "Mã hóa đơn", "Ngày giao dịch", "Giờ", "Status", "so_luong", "Trị giá", "Hàng"),
       "biz_117": ([("SoBanHang.xlsx", 2)], "Mã phiếu", "Ngày lập", "Giờ", "Ghi chú TT", "Số lượng hàng", "Tiền", "Tên SP")}
for shop, (files, oc, dc, tc, sc, qc, ac, pc) in CFG.items():
    rows = [r for f, hr in files for r in load(shop, f, None, hr) if r.get(oc)]
    by = defaultdict(list)
    for r in rows: by[r[oc]].append(r)
    neg = {o: ls for o, ls in by.items() if any((qty(l[qc]) or 0) < 0 for l in ls)}
    st_neg = Counter(); st_all_ret = Counter(); kinds = Counter(); datediff = Counter()
    for o, ls in by.items():
        sts = tuple(sorted({str(l[sc]) for l in ls}))
        if o in neg: st_neg[sts] += 1
    for o, ls in neg.items():
        pos = [l for l in ls if (qty(l[qc]) or 0) > 0]; ng = [l for l in ls if (qty(l[qc]) or 0) < 0]
        kinds["has sale lines" if pos else "ONLY return lines"] += 1
        if pos:
            dp = {day(l[dc]) for l in pos}; dn = {day(l[dc]) for l in ng}
            datediff["same date" if dn <= dp else "return line has other date"] += 1
    print(f"## {shop}: orders={len(by)}, orders with negative lines={len(neg)}")
    print("   statuses (set per order) of orders with negative lines:", st_neg.most_common(6))
    print("  ", dict(kinds), dict(datediff))
    ret_words = Counter(str(l[sc]) for ls in by.values() for l in ls)
    print("   all status values:", ret_words.most_common())
    for o in list(neg)[:3]:
        print("   order", o)
        for l in by[o]:
            print("     ", l["_sheet"], l["_row"], "|", l[dc], "|", l[tc], "|", l[sc], "|", l[pc], "|", l[qc], "|", l[ac])
