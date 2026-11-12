"""Shared helpers for read-only checks on raw sales_lines files."""
import csv, re, sqlite3, datetime as dt
from pathlib import Path
import openpyxl

ROOT = Path("dataset/zaloAI/train/data")


def money(v):
    """Parse VND amounts: 13,353,000 / 55.000đ / 25k / 1tr440 / 16,56tr / 25,000.0000 / -53.500đ."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace("đ", "").replace(" ", "")
    neg = s.startswith("-")
    s = s.lstrip("-")
    if m := re.fullmatch(r"(\d+)tr(\d+)", s):
        x = int(m[1]) * 1e6 + int(m[2].ljust(3, "0")[:3]) * 1000
    elif re.fullmatch(r"[\d.,]+tr", s):
        x = float(s[:-2].replace(",", ".")) * 1e6
    elif re.fullmatch(r"[\d.,]+k", s):
        x = float(s[:-1].replace(",", "")) * 1000
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        x = float(s.replace(".", ""))
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?|\d+(\.\d+)?", s):
        x = float(s.replace(",", ""))
    else:
        raise ValueError(f"money {v!r}")
    return -x if neg else x


def qty(v):
    """'3 chai' -> 3.0, 2 -> 2.0, '-1' -> -1.0"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(v))
    if not m:
        raise ValueError(f"qty {v!r}")
    return float(m[1].replace(",", "."))


def day(d):
    if d is None or d == "":
        return None
    if isinstance(d, dt.datetime):
        return d.date()
    if isinstance(d, (int, float)):
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(d))
    s = str(d).strip()
    if m := (re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s) or re.match(r"(\d{1,2}) thg (\d{1,2}) (\d{4})", s)):
        return dt.date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", s):
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    return s  # e.g. '1/1' without year: returned as text


def load(shop, fname, sheet=None, header_row=1):
    """Rows as dicts (+ '_sheet', '_row'), from xlsx (all sheets unless `sheet`), csv or sqlite table."""
    path = ROOT / shop / fname
    out = []
    if path.suffix == ".xlsx":
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sn in ([sheet] if sheet else wb.sheetnames):
            rows = wb[sn].iter_rows(values_only=True)
            for _ in range(header_row - 1):
                next(rows)
            hdr = [str(h) if h is not None else "" for h in next(rows)]
            for i, r in enumerate(rows, start=header_row + 1):
                if r and any(v not in (None, "") for v in r):
                    d = dict(zip(hdr, r))
                    d["_sheet"], d["_row"] = sn, i
                    out.append(d)
        wb.close()
    elif path.suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for i, d in enumerate(csv.DictReader(fh), start=2):
                d["_sheet"], d["_row"] = "", i
                out.append(d)
    else:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = con.execute(f'select * from "{sheet}"')
        hdr = [c[0] for c in cur.description]
        for i, r in enumerate(cur, start=1):
            d = dict(zip(hdr, r))
            d["_sheet"], d["_row"] = sheet, i
            out.append(d)
        con.close()
    return out
