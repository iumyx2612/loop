"""Read-only: per shop/file/sheet, find the header row, then report every column with <=30 distinct
values (statuses, channels, units, ...) plus row counts, and format samples for date/money-ish columns."""
import sys, sqlite3, csv, re
from collections import Counter, defaultdict
from pathlib import Path
import openpyxl

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
shops = sys.argv[2:] or sorted(p.name for p in root.iterdir() if p.is_dir())
MAXD = 30
SKIP_LOWCARD = re.compile(r"^(STT|No\.|Thứ tự|TT dòng|SoThuTu)$")


def pick_header(rows):
    best, bi = -1, 0
    for i, r in enumerate(rows[:6]):
        n = sum(1 for v in r if v not in (None, ""))
        if n > best:
            best, bi = n, i
    return bi


def shape(v):
    """Coarse format signature of a cell value, to show how mixed a column is."""
    if v is None or v == "":
        return "∅"
    if not isinstance(v, str):
        return type(v).__name__
    s = re.sub(r"\d", "9", v.strip())
    s = re.sub(r"9+", "9", s)
    return "s:" + s[:20]


def report(label, header, rows):
    cols = defaultdict(Counter)
    shapes = defaultdict(Counter)
    n = 0
    for r in rows:
        if r is None or all(v in (None, "") for v in r):
            continue
        n += 1
        for h, v in zip(header, r):
            if len(cols[h]) <= MAXD:
                cols[h][v] += 1
            shapes[h][shape(v)] += 1
    print(f"== {label}  rows={n}")
    for h in header:
        c = cols[h]
        sh = shapes[h]
        fmt = ", ".join(f"{k}×{v}" for k, v in sh.most_common(6))
        if len(c) <= MAXD and not SKIP_LOWCARD.match(str(h)) and len(c) > 1:
            vals = ", ".join(f"{k!r}×{v}" for k, v in c.most_common(MAXD))
            print(f"   [{h}] LOW: {vals}")
        elif len(sh) > 1:
            print(f"   [{h}] shapes: {fmt}")


for shop in shops:
    d = root / shop
    print(f"\n######## {shop}")
    for f in sorted(d.iterdir()):
        if f.name.startswith("~$") or f.suffix == ".md":
            continue
        if f.suffix == ".xlsx":
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            groups = {}  # header -> list of row iterators (merge month sheets)
            for sn in wb.sheetnames:
                allrows = list(wb[sn].iter_rows(values_only=True))
                hi = pick_header(allrows)
                hdr = tuple(allrows[hi]) if allrows else ()
                pre = [r for r in allrows[:hi] if any(v not in (None, "") for v in r)]
                if pre:
                    print(f"   (sheet {sn!r}: header at row {hi + 1}; above: {pre[:2]})")
                groups.setdefault(hdr, []).append((sn, allrows[hi + 1:]))
            for hdr, parts in groups.items():
                names = [p[0] for p in parts]
                lbl = f"{f.name} [{names[0]}{' … ' + names[-1] if len(names) > 1 else ''}] ({len(names)} sheets)"
                report(lbl, hdr, (r for _, rs in parts for r in rs))
            wb.close()
        elif f.suffix == ".csv":
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rd = csv.reader(fh)
                hdr = next(rd)
                report(f.name, hdr, rd)
        elif f.suffix == ".sqlite":
            con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for (t,) in con.execute("select name from sqlite_master where type='table' order by name"):
                cur = con.execute(f'select * from "{t}"')
                hdr = [c[0] for c in cur.description]
                report(f"table {t}", hdr, cur)
            con.close()
