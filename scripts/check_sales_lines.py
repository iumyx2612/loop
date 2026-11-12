"""Check converted sales_lines tables against the train answer key, on question kinds that are easy to
recognise with a regex: revenue of a month / quarter / day, number of cancelled orders, cancellation rate.
Answers are computed with plain pandas from data_std/<split>/<shop>/sales_lines.parquet (no agent involved).

Usage: .venv/Scripts/python.exe -I scripts/check_sales_lines.py [shop ...] [-v]
"""
import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.parse import fold_accents  # noqa: E402

SPLIT = "train"
DATA = ROOT / "dataset" / "zaloAI" / SPLIT

REVENUE = re.compile(r"doanh thu|dthu|ban (duoc|đuoc|dc|ra)?.*(tien|total)|tien ban|tien hang")
CANCEL = re.compile(r"\bh(u|o)y\b|huy don|bi huy|da huy")
RATE = re.compile(r"ti le|tir le|%|phan tram")
# Questions about one product / group / customer / channel / shift are not checked here
NARROW = re.compile(r"\bnhom\b|khach|kenh|\bca\b|zalo|nhan vien|ngay nao|cao nhat|nhieu nhat|mat hang|san pham|mon\b|so voi|tang|giam"
                    r"|trung binh|binh quan|moi don|tai quay|nhap|online|dien thoai|giao hang|\bno\b")


def month_range(y, m):
    start = dt.date(y, m, 1)
    end = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return start, end


def period(q: str, today: dt.date):
    """(start, end) of the period a question names, or None. Covers: tháng M/YYYY, tháng M năm YYYY,
    quý Q/YYYY, quý Q năm YYYY, tháng này, hôm nay, hôm qua (rulebook §2)."""
    if m := re.search(r"thang (\d{1,2})\s*(?:/|nam)\s*(\d{4})", q):
        return month_range(int(m[2]), int(m[1]))
    if m := re.search(r"quy (\d)\s*(?:/|nam)\s*(\d{4})", q):
        y, k = int(m[2]), int(m[1])
        return dt.date(y, 3 * k - 2, 1), month_range(y, 3 * k)[1]
    if re.search(r"thang nay", q):
        return today.replace(day=1), today
    if re.search(r"hom nay", q):
        return today, today
    if re.search(r"hom qua|bua qua|hqua", q):
        d = today - dt.timedelta(days=1)
        return d, d
    return None


def answer(kind: str, lines: pd.DataFrame, start, end):
    in_period = lines[(lines["order_date"] >= pd.Timestamp(start)) & (lines["order_date"] <= pd.Timestamp(end))]
    orders = in_period.drop_duplicates("order_id")
    if kind == "revenue":  # rulebook §1: orders not cancelled and not returned; in-delivery counts
        return int(in_period.loc[in_period["status"].isin(["completed", "in_delivery"]), "line_amount"].sum())
    if kind == "cancel_count":
        return int((orders["status"] == "cancelled").sum())
    if kind == "cancel_rate":  # §1: share of all orders placed in the period, any status; 2 decimals
        return round(100 * (orders["status"] == "cancelled").sum() / len(orders), 2) if len(orders) else None


def main(shops: list[str], verbose: bool) -> None:
    key = {j["id"]: j for j in map(json.loads, open(DATA / "answers.jsonl", encoding="utf-8"))}
    questions = [json.loads(l) for l in open(DATA / "questions.jsonl", encoding="utf-8")]
    total = Counter()
    for shop in shops:
        lines = pd.read_parquet(ROOT / "data_std" / SPLIT / shop / "sales_lines.parquet")
        product_names = {fold_accents(n) for n in lines["product_name"].dropna().unique()}
        stats, misses = Counter(), []
        for q in (q for q in questions if q["business_id"] == shop):
            text = fold_accents(q["question"])
            if NARROW.search(text) or any(n in text for n in product_names):
                continue
            if key[q["id"]]["type"] not in ("number", "percent", "unanswerable"):
                continue
            kind = ("cancel_rate" if RATE.search(text) else "cancel_count") if CANCEL.search(text) else \
                   ("revenue" if REVENUE.search(text) and not RATE.search(text) else None)
            span = period(text, dt.date.fromisoformat(q["as_of_date"]))
            if kind is None or span is None:
                continue
            k = key[q["id"]]
            got = answer(kind, lines, *span)
            ok = k["type"] != "unanswerable" and got is not None and abs(float(k["answer"]) - got) < 0.005
            stats[(kind, ok)] += 1
            if not ok:
                misses.append(f"   MISS {q['id']} {kind} {span[0]}..{span[1]} | {q['question']} | key={k.get('answer', k['type'])} got={got}")
        summary = ", ".join(f"{kind} {stats[(kind, True)]}/{stats[(kind, True)] + stats[(kind, False)]}"
                            for kind in ("revenue", "cancel_count", "cancel_rate") if stats[(kind, True)] + stats[(kind, False)])
        print(f"## {shop}: {summary}")
        for line in misses if verbose else misses[:8]:
            print(line)
        total.update(stats)
    ok = sum(v for (_, good), v in total.items() if good)
    print(f"\nall: {ok}/{sum(total.values())} correct")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "-v"]
    main(args or ["biz_108", "biz_116", "biz_117", "biz_122", "biz_128"], "-v" in sys.argv)
