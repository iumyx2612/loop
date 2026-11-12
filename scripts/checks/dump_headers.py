"""Read-only: per shop, every file/sheet(group)/table with its header columns,
the row above the header (two-level headers), and the README meaning of each column.
Output: JSON list to stdout."""
import sys, json, csv, re, sqlite3
from pathlib import Path
import openpyxl
sys.stdout.reconfigure(encoding="utf-8")
root = Path(sys.argv[1])
shops = sys.argv[2:] or sorted(p.name for p in root.iterdir() if p.is_dir())


def readme_cols(path):
    """{section title: {col: meaning}} from '## Cột trong `X`...' sections."""
    out, cur = {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            cur = line[3:].strip() if line.startswith("## Cột") else None
            if cur:
                out[cur] = {}
        elif cur and (m := re.match(r"-\s*\*\*(.+?)\*\*\s*:?\s*(.*)", line)):
            out[cur][m[1]] = m[2]
    return out


def pick_header(rows):
    best, bi = -1, 0
    for i, r in enumerate(rows[:8]):
        n = sum(1 for v in r if v not in (None, ""))
        if n > best:
            best, bi = n, i
    return bi


res = []
for shop in shops:
    d = root / shop
    rm = readme_cols(d / "README.md") if (d / "README.md").exists() else {}
    files = []
    for f in sorted(d.iterdir()):
        if f.name.startswith("~$") or f.suffix == ".md":
            continue
        if f.suffix == ".xlsx":
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            groups = {}
            for sn in wb.sheetnames:
                rows = list(wb[sn].iter_rows(max_row=10, values_only=True))
                if not rows:
                    continue
                hi = pick_header(rows)
                hdr = tuple("" if v is None else str(v) for v in rows[hi])
                above = [tuple(v for v in r if v not in (None, "")) for r in rows[:hi]]
                g = groups.setdefault(hdr, {"sheets": [], "above": above, "header_row": hi + 1})
                g["sheets"].append(sn)
            for hdr, g in groups.items():
                files.append({"file": f.name, **g, "columns": [c for c in hdr if c]})
            wb.close()
        elif f.suffix == ".csv":
            with open(f, encoding="utf-8-sig", newline="") as fh:
                rows = [r for _, r in zip(range(10), csv.reader(fh))]
            hi = pick_header(rows)
            files.append({"file": f.name, "sheets": [], "above": [r for r in rows[:hi] if any(r)],
                          "header_row": hi + 1, "columns": [c for c in rows[hi] if c]})
        elif f.suffix in (".sqlite", ".db", ".sqlite3"):
            con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            for (t,) in con.execute("select name from sqlite_master where type='table' order by name"):
                cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
                files.append({"file": f.name, "sheets": [t], "above": [], "header_row": 0, "columns": cols})
            con.close()
        else:
            files.append({"file": f.name, "sheets": [], "above": [], "header_row": 0, "columns": ["<unknown type>"]})
    res.append({"shop": shop, "readme": rm, "files": files})
print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
