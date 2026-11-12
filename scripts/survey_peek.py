"""Read-only peek: per shop, per file/sheet -> sheet names, first raw rows, row count."""
import sys, os, sqlite3, csv
from pathlib import Path
import openpyxl

sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
shops = sys.argv[2:] or sorted(p.name for p in root.iterdir() if p.is_dir())
NROWS = 3


def short(v):
    s = repr(v)
    return s if len(s) <= 40 else s[:37] + "...'"


for shop in shops:
    d = root / shop
    print(f"\n######## {shop}")
    for f in sorted(d.iterdir()):
        if f.name.startswith("~$") or f.suffix == ".md":
            continue
        if f.suffix == ".xlsx":
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            names = wb.sheetnames
            print(f"== {f.name}  sheets({len(names)}): {names[:4]}{' ...' if len(names) > 4 else ''}")
            seen_headers = {}
            for i, sn in enumerate(names):
                ws = wb[sn]
                rows = list(ws.iter_rows(min_row=1, max_row=NROWS + 1, values_only=True))
                hdr = tuple(rows[0]) if rows else ()
                if hdr in seen_headers and i > 0:
                    continue  # same header as an earlier sheet: skip repeats
                seen_headers[hdr] = sn
                print(f"  -- sheet {sn!r} max_row={ws.max_row}")
                for r in rows:
                    print("    ", " | ".join(short(v) for v in r))
            if len(seen_headers) == 1 and len(names) > 1:
                print("  (all sheets share the same header)")
            wb.close()
        elif f.suffix == ".csv":
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rd = csv.reader(fh)
                rows = [next(rd) for _ in range(NROWS + 1)]
                n = sum(1 for _ in rd) + NROWS
            print(f"== {f.name}  data_rows={n}")
            for r in rows:
                print("    ", " | ".join(short(v) for v in r))
        elif f.suffix == ".sqlite":
            con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for (t,) in con.execute("select name from sqlite_master where type='table' order by name"):
                cur = con.execute(f'select * from "{t}" limit 2')
                cols = [c[0] for c in cur.description]
                n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
                print(f"== table {t} rows={n}")
                print("    ", " | ".join(cols))
                for r in cur.fetchall():
                    print("    ", " | ".join(short(v) for v in r))
            con.close()
        else:
            print(f"== {f.name} (unhandled)")
