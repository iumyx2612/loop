"""Usage: status_month.py <xlsx> <date_col> <order_col> <status_col> [sheet...]
Prints, per month, distinct-order counts for each raw status."""
import sys, re, datetime as dt
from collections import defaultdict, Counter
import openpyxl
sys.stdout.reconfigure(encoding="utf-8")
path, dc, oc, sc, *sheets = sys.argv[1:]


def month(d):
    if isinstance(d, (dt.datetime, dt.date)):
        return d.year, d.month
    if isinstance(d, (int, float)):
        x = dt.date(1899, 12, 30) + dt.timedelta(days=int(d))
        return x.year, x.month
    s = str(d)
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", s) or re.match(r"\s*(\d{1,2}) thg (\d{1,2}) (\d{4})", s)
    if m:
        return int(m[3]), int(m[2])
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return int(m[1]), int(m[2])
    return None


wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
orders = {}  # order -> (month, status)
bad = Counter()
for sn in (sheets or wb.sheetnames):
    rows = list(wb[sn].iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows[:8]) if dc in r and sc in r)
    h = rows[hi]
    di, oi, si = h.index(dc), h.index(oc), h.index(sc)
    for r in rows[hi + 1:]:
        if r is None or r[oi] in (None, ""):
            continue
        m = month(r[di])
        if m is None:
            bad[repr(r[di])[:30]] += 1
            continue
        orders.setdefault(r[oi], (m, r[si]))
if bad:
    print("BAD DATES:", bad.most_common(5))
tab = defaultdict(Counter)
for m, s in orders.values():
    tab[m][s] += 1
allst = sorted({s for c in tab.values() for s in c}, key=str)
print("month   total  " + "  ".join(map(str, allst)))
for m in sorted(tab):
    print(f"{m[0]}-{m[1]:02d}  {sum(tab[m].values()):5d}  " + "  ".join(f"{tab[m][s]}" for s in allst))
