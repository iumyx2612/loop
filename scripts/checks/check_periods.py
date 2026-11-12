"""Hand-worked cases for src/datamate/query_agent/periods.py (rulebook §2). Run:
.venv/Scripts/python.exe -I scripts/checks/check_periods.py
"""
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.datamate.query_agent.periods import LunarCalendar, PeriodError, previous_period, resolve_period  # noqa: E402

D = dt.date.fromisoformat
LUNAR = LunarCalendar(ROOT / "dataset/zaloAI/train/calendar.csv")

# (spec, as_of, expected window, expected previous window or None)
CASES = [
    # 108 "dthu thang 2/2026 hon hay kem th truoc" → whole Feb vs whole Jan
    ({"kind": "month", "month": 2, "year": 2026}, "2026-05-29", ("2026-02-01", "2026-02-28"), ("2026-01-01", "2026-01-31")),
    # month without year: not after today → this year, else last year
    ({"kind": "month", "month": 5}, "2026-05-20", ("2026-05-01", "2026-05-20"), ("2026-04-01", "2026-04-30")),
    ({"kind": "month", "month": 11}, "2026-05-20", ("2025-11-01", "2025-11-30"), ("2025-10-01", "2025-10-31")),
    # 117 "từ đầu tháng này ... cùng kỳ tháng trước" (as_of 2026-05-07)
    ({"kind": "this_month"}, "2026-05-07", ("2026-05-01", "2026-05-07"), ("2026-04-01", "2026-04-07")),
    # tháng này on the 31st: last month has no 31st → up to its last day
    ({"kind": "this_month"}, "2026-03-31", ("2026-03-01", "2026-03-31"), ("2026-02-01", "2026-02-28")),
    ({"kind": "this_month"}, "2026-01-15", ("2026-01-01", "2026-01-15"), ("2025-12-01", "2025-12-15")),
    # weeks start Monday; 2025-07-06 is a Sunday
    ({"kind": "last_week"}, "2025-07-06", ("2025-06-23", "2025-06-29"), ("2025-06-16", "2025-06-22")),
    ({"kind": "this_week"}, "2025-07-06", ("2025-06-30", "2025-07-06"), ("2025-06-23", "2025-06-29")),
    ({"kind": "this_week"}, "2025-07-07", ("2025-07-07", "2025-07-07"), ("2025-07-06", "2025-07-06")),
    # "30 ngày gần nhất" includes today
    ({"kind": "last_n_days", "n": 30}, "2025-04-25", ("2025-03-27", "2025-04-25"), ("2025-02-25", "2025-03-26")),
    ({"kind": "today"}, "2026-07-06", ("2026-07-06", "2026-07-06"), ("2026-07-05", "2026-07-05")),
    ({"kind": "yesterday"}, "2026-07-08", ("2026-07-07", "2026-07-07"), ("2026-07-06", "2026-07-06")),
    ({"kind": "year_to_date"}, "2025-10-18", ("2025-01-01", "2025-10-18"), ("2024-01-01", "2024-10-18")),
    ({"kind": "quarter", "quarter": 1, "year": 2026}, "2026-05-29", ("2026-01-01", "2026-03-31"), ("2025-10-01", "2025-12-31")),
    # quarter without year, current quarter: cut at today
    ({"kind": "quarter", "quarter": 2}, "2026-05-29", ("2026-04-01", "2026-05-29"), ("2026-01-01", "2026-03-31")),
    # price-increase effect: 14 days from D (incl. D) vs the 14 days before
    ({"kind": "range", "from": "2026-04-18", "to": "2026-05-01"}, "2026-05-20", ("2026-04-18", "2026-05-01"), ("2026-04-04", "2026-04-17")),
    ({"kind": "day", "date": "2026-02-25"}, "2026-05-20", ("2026-02-25", "2026-02-25"), ("2026-02-24", "2026-02-24")),
    # Tết 2026: 23 tháng Chạp = 2026-02-10, mùng 10 tháng Giêng = 2026-02-26 (17 days)
    ({"kind": "tet", "year": 2026}, "2026-09-03", ("2026-02-10", "2026-02-26"), ("2026-01-24", "2026-02-09")),
    ({"kind": "all_before", "date": "2025-11-29"}, "2026-09-03", (None, "2025-11-28"), None),
]

# (spec, as_of): must raise PeriodError
ERRORS = [
    ({"kind": "day", "date": "2026-06-01"}, "2026-05-20"),   # after today
    ({"kind": "month", "month": 13}, "2026-05-20"),
    ({"kind": "month"}, "2026-05-20"),
    ({"kind": "fortnight"}, "2026-05-20"),
    ({"kind": "range", "from": "2026-05-02", "to": "2026-05-01"}, "2026-05-20"),
]


def as_pair(w):
    return (w.start.isoformat() if w.start else None, w.end.isoformat())


failures = 0
for spec, as_of, want, want_prev in CASES:
    got = as_pair(resolve_period(spec, D(as_of), LUNAR))
    got_prev = None if want_prev is None else as_pair(previous_period(spec, D(as_of), LUNAR))
    if got != want or got_prev != want_prev:
        failures += 1
        print(f"FAIL {spec} as_of {as_of}: got {got} / {got_prev}, want {want} / {want_prev}")
for spec, as_of in ERRORS:
    try:
        resolve_period(spec, D(as_of), LUNAR)
        failures += 1
        print(f"FAIL {spec} as_of {as_of}: no error")
    except PeriodError:
        pass
print(f"{len(CASES) + len(ERRORS) - failures}/{len(CASES) + len(ERRORS)} ok")
