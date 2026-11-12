"""Read-only: markdown report of every column in every sales_lines file of the train shops,
with the README meaning and sample values. Usage: sales_lines_report.py <data_dir> <headers.json>"""
import sys, json, csv, sqlite3, re, datetime as dt
from pathlib import Path
import openpyxl
sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
info = {s["shop"]: s for s in json.load(open(sys.argv[2], encoding="utf-8"))}

# shop -> list of (file, sheet or None); one row = one product line of an order
SALES_LINES = {
    "biz_101": [("ban_hang.csv", None)],
    "biz_102": [("DonHang_ChiTiet.xlsx", None)],
    "biz_103": [("DonHang_ChiTiet.xlsx", None)],
    "biz_104": [("DonHang_ChiTiet.xlsx", None)],
    "biz_105": [("SoBanHang_1.xlsx", None), ("SoBanHang_2026.xlsx", None)],
    "biz_106": [("DanhSachHoaDonChiTiet_1.xlsx", None), ("DanhSachHoaDonChiTiet_2.xlsx", None)],
    "biz_107": [("SoBanHang.xlsx", None)],
    "biz_108": [("DanhSachHoaDonChiTiet.xlsx", None)],
    "biz_109": [("DanhSachHoaDonChiTiet.xlsx", None)],
    "biz_111": [("chi_tiet_don.csv", None)],
    "biz_112": [("So_chi_tiet_ban_hang_1.xlsx", None), ("So_chi_tiet_ban_hang_2026.xlsx", None)],
    "biz_113": [("SoBanHang.xlsx", None)],
    "biz_114": [("ban_hang.csv", None)],
    "biz_115": [("So_chi_tiet_ban_hang.xlsx", None)],
    "biz_116": [("Bao_cao_ban_hang.xlsx", "So_chi_tiet_ban_hang")],
    "biz_117": [("SoBanHang.xlsx", None)],
    "biz_118": [("So_chi_tiet_ban_hang.xlsx", None)],
    "biz_119": [("DanhSachHoaDonChiTiet.xlsx", None)],
    "biz_120": [("DanhSachHoaDonChiTiet.xlsx", None)],
    "biz_121": [("DonHang_ChiTiet.xlsx", None)],
    "biz_122": [("DonHang_ChiTiet_1.xlsx", None), ("DonHang_ChiTiet_2026.xlsx", None)],
    "biz_123": [("pos.sqlite", "chi_tiet_don")],
    "biz_124": [("BanHang_ChiTietHoaDon.xlsx", None)],
    "biz_125": [("ban_hang.csv", None)],
    "biz_126": [("SoBanHang.xlsx", None)],
    "biz_127": [("chi_tiet_don.csv", None)],
    "biz_128": [("DanhSachHoaDonChiTiet.xlsx", None)],
    "biz_129": [("chi_tiet_don.csv", None)],
    "biz_130": [("So_chi_tiet_ban_hang_1.xlsx", None), ("So_chi_tiet_ban_hang_2.xlsx", None)],
}
# shops whose order-level fields live in a separate orders file (not in sales_lines)
ORDER_FILE = {"biz_106": "DanhSachHoaDon.xlsx", "biz_108": "DanhSachHoaDon.xlsx",
              "biz_109": "DuLieuBanHang.xlsx [DanhSachHoaDon]", "biz_111": "don_hang.csv",
              "biz_119": "DanhSachHoaDon.xlsx", "biz_120": "DanhSachHoaDon.xlsx",
              "biz_123": "pos.sqlite [don_hang]", "biz_124": "BanHang_HoaDon.xlsx",
              "biz_127": "don_hang.csv", "biz_128": "DanhSachHoaDon.xlsx", "biz_129": "don_hang.csv"}
NSAMPLE = 4


def fmt(v):
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S") if (v.hour or v.minute or v.second) else v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = repr(v) if isinstance(v, str) else str(v)
    return s.replace("|", "\\|")[:40]


def samples(shop, fname, sheet, cols, header_row):
    """{col: [distinct sample values]}, spread over the file (every k-th row)."""
    path = root / shop / fname
    out = {c: [] for c in cols}
    rows, hdr = [], None
    if path.suffix == ".xlsx":
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sn in ([sheet] if sheet else wb.sheetnames):
            it = wb[sn].iter_rows(values_only=True)
            for _ in range(header_row - 1):
                next(it, None)
            h = next(it, None)
            hdr = hdr or ["" if v is None else str(v) for v in h]
            rows.extend(r for r in it if r and any(v not in (None, "") for v in r))
            if len(rows) > 20000:
                break
        wb.close()
    elif path.suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rd = csv.reader(fh)
            hdr = next(rd)
            rows = [r for _, r in zip(range(20000), rd)]
    else:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.execute(f'select * from "{sheet}" limit 20000')
        hdr = [d[0] for d in cur.description]
        rows = cur.fetchall()
        con.close()
    step = max(1, len(rows) // 400)
    for r in rows[::step]:
        for i, c in enumerate(hdr):
            if c in out and i < len(r) and r[i] not in (None, "") and len(out[c]) < NSAMPLE:
                f = fmt(r[i])
                if f not in out[c]:
                    out[c].append(f)
    return out


def meaning(readme, fname, sheet, col):
    stem = re.sub(r"(_\d+|_\d{4})?\.\w+$", "", fname)
    best = None
    for title, cols in readme.items():
        if col in cols:
            if fname in title and (sheet is None or sheet in title or "sheet" not in title):
                return cols[col]
            if stem in title:
                best = best or cols[col]
    return best


lines = ["# sales_lines: every column in every train shop", "",
         "One row = one product line of an order. Generated read-only from the raw files "
         "(header row found automatically) and the README column lists.", "",
         "- **README meaning**: the shop's README description of the column; `—` = README has no column list "
         "(biz_106, 109, 123, 124) or does not mention the column.",
         "- **Samples**: up to 4 distinct values spread over the file, shown as Python values "
         "(`'...'` = text cell, bare number = numeric cell).",
         "- Shops where order fields (date, status, customer, ...) live in a separate orders file are marked; "
         "those fields are not in the tables below.",
         "- biz_110 has no sales_lines file (orders only).", ""]
for shop, files in SALES_LINES.items():
    s = info[shop]
    lines.append(f"## {shop}")
    if shop in ORDER_FILE:
        lines.append(f"Order fields are in a separate orders file: `{ORDER_FILE[shop]}`.")
    if not s["readme"]:
        lines.append("README has no column list.")
    seen_hdr = None
    for fname, sheet in files:
        entries = [f for f in s["files"] if f["file"] == fname and (sheet is None or sheet in f["sheets"])]
        for e in entries:
            sh = e["sheets"]
            shtxt = f", sheets {sh[0]} … {sh[-1]} ({len(sh)})" if len(sh) > 1 else (f", sheet {sh[0]}" if sh else "")
            lines.append("")
            lines.append(f"**`{fname}`**{shtxt}, header on row {e['header_row']}")
            if e["above"] and any(e["above"]):
                ab = " / ".join(" · ".join(map(str, r)) for r in e["above"] if r)
                lines.append(f"Rows above the header: {ab}")
            if seen_hdr == e["columns"]:
                lines.append("Same columns as the previous file.")
                continue
            seen_hdr = e["columns"]
            smp = samples(shop, fname, sheet or (sh[0] if sh and fname.endswith('.sqlite') else None),
                          e["columns"], e["header_row"])
            lines.append("")
            lines.append("| Column | README meaning | Samples |")
            lines.append("|---|---|---|")
            for c in e["columns"]:
                m = meaning(s["readme"], fname, sheet, c) or "—"
                lines.append(f"| `{c}` | {m} | {', '.join(smp.get(c, [])) or '(empty)'} |")
    lines.append("")
print("\n".join(lines))
