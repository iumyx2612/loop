"""Period specs → date windows, relative to a question's as_of_date (rulebook §2).

A period spec is a small dict the agent fills, e.g. {"kind": "month", "month": 7, "year": 2025}.
`resolve_period` turns it into the window asked about, `previous_period` into the window it is
compared with ("so với kỳ trước"). All windows are inclusive on both ends. Days after as_of are
cut off: the rulebook treats records after "today" as not yet happened.
"""
import calendar as cal
import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

KINDS = ("day", "today", "yesterday", "this_week", "last_week", "this_month", "month", "quarter",
         "year", "year_to_date", "last_n_days", "range", "all_before", "tet")


class PeriodError(ValueError):
    """A spec the resolver cannot turn into a window; the message is meant for the agent."""


@dataclass(frozen=True)
class Window:
    start: dt.date | None  # None = from the first day of data
    end: dt.date
    label: str

    def __str__(self):
        start = self.start.isoformat() if self.start else "start of data"
        return f"{self.label}: {start} → {self.end.isoformat()}"


class LunarCalendar:
    """Lunar → solar lookup from the split's calendar.csv (columns date, lunar_day, lunar_month, lunar_year, lunar_leap)."""

    def __init__(self, path: Path):
        self._solar = {}
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["lunar_leap"] == "false":
                    key = (int(row["lunar_year"]), int(row["lunar_month"]), int(row["lunar_day"]))
                    self._solar[key] = dt.date.fromisoformat(row["date"])

    def solar(self, year: int, month: int, day: int) -> dt.date:
        try:
            return self._solar[(year, month, day)]
        except KeyError:
            raise PeriodError(f"lunar date {day}/{month}/{year} is outside calendar.csv") from None


def _date(v, field) -> dt.date:
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise PeriodError(f"{field} must be a date YYYY-MM-DD, got {v!r}") from None


def _int(spec, field) -> int:
    if field not in spec:
        raise PeriodError(f"period kind {spec['kind']!r} needs {field!r}")
    try:
        return int(spec[field])
    except (TypeError, ValueError):
        raise PeriodError(f"{field} must be an integer, got {spec[field]!r}") from None


def _month_end(year, month) -> dt.date:
    return dt.date(year, month, cal.monthrange(year, month)[1])


def _add_months(year, month, n) -> tuple[int, int]:
    i = year * 12 + month - 1 + n
    return i // 12, i % 12 + 1


def _year_not_after(spec, start_of, as_of) -> int:
    """§2: month / quarter without a year is this year's if it does not start after today, else last year's."""
    if spec.get("year") is not None:
        return _int(spec, "year")
    return as_of.year if start_of(as_of.year) <= as_of else as_of.year - 1


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _tet_start(year, lunar) -> dt.date:
    if lunar is None:
        raise PeriodError("Tết periods need calendar.csv")
    return lunar.solar(year - 1, 12, 23)


def _window(spec: dict, as_of: dt.date, lunar: LunarCalendar | None) -> Window:
    """The window a spec names, before cutting at as_of."""
    kind = spec.get("kind")
    day = dt.timedelta(days=1)
    if kind == "day":
        d = _date(spec.get("date"), "date")
        return Window(d, d, f"day {d}")
    if kind == "today":
        return Window(as_of, as_of, "today")
    if kind == "yesterday":
        return Window(as_of - day, as_of - day, "yesterday")
    if kind == "this_week":
        return Window(_monday(as_of), as_of, "this week (Monday → today)")
    if kind == "last_week":
        mon = _monday(as_of) - 7 * day
        return Window(mon, mon + 6 * day, "last week (Mon–Sun)")
    if kind == "this_month":
        return Window(as_of.replace(day=1), as_of, "this month (1st → today)")
    if kind == "month":
        m = _int(spec, "month")
        if not 1 <= m <= 12:
            raise PeriodError(f"month must be 1–12, got {m}")
        y = _year_not_after(spec, lambda y: dt.date(y, m, 1), as_of)
        return Window(dt.date(y, m, 1), _month_end(y, m), f"month {m}/{y}")
    if kind == "quarter":
        q = _int(spec, "quarter")
        if not 1 <= q <= 4:
            raise PeriodError(f"quarter must be 1–4, got {q}")
        y = _year_not_after(spec, lambda y: dt.date(y, 3 * q - 2, 1), as_of)
        return Window(dt.date(y, 3 * q - 2, 1), _month_end(y, 3 * q), f"quarter {q}/{y}")
    if kind == "year":
        y = _int(spec, "year")
        return Window(dt.date(y, 1, 1), dt.date(y, 12, 31), f"year {y}")
    if kind == "year_to_date":
        return Window(dt.date(as_of.year, 1, 1), as_of, "year to date")
    if kind == "last_n_days":
        n = _int(spec, "n")
        if n < 1:
            raise PeriodError(f"n must be ≥ 1, got {n}")
        return Window(as_of - (n - 1) * day, as_of, f"last {n} days (incl. today)")
    if kind == "range":
        a, b = _date(spec.get("from"), "from"), _date(spec.get("to"), "to")
        if a > b:
            raise PeriodError(f"range from {a} is after to {b}")
        return Window(a, b, f"range {a} → {b}")
    if kind == "all_before":
        d = _date(spec.get("date"), "date")
        return Window(None, d - day, f"everything before {d}")
    if kind == "tet":
        y = _int(spec, "year")
        end = lunar.solar(y, 1, 10) if lunar else None
        return Window(_tet_start(y, lunar), end, f"Tết {y} (23 tháng Chạp → mùng 10 tháng Giêng)")
    raise PeriodError(f"unknown period kind {kind!r}; expected one of {', '.join(KINDS)}")


def _cut(w: Window, as_of: dt.date) -> Window:
    if w.start is not None and w.start > as_of:
        raise PeriodError(f"{w.label} starts after today ({as_of})")
    return Window(w.start, min(w.end, as_of), w.label) if w.end > as_of else w


def resolve_period(spec: dict, as_of: dt.date, lunar: LunarCalendar | None = None) -> Window:
    return _cut(_window(spec, as_of, lunar), as_of)


def previous_period(spec: dict, as_of: dt.date, lunar: LunarCalendar | None = None) -> Window:
    """The window a period is compared with (§1 "so với kỳ trước", §2 "tháng này", "trước Tết")."""
    kind = spec.get("kind")
    w = resolve_period(spec, as_of, lunar)
    day = dt.timedelta(days=1)
    if kind == "all_before":
        raise PeriodError("all_before has no previous period")
    if kind == "this_month":
        # Same day numbers of last month; if last month is shorter, up to its last day
        y, m = _add_months(as_of.year, as_of.month, -1)
        end = min(dt.date(y, m, 1) + (as_of.day - 1) * day, _month_end(y, m))
        return Window(dt.date(y, m, 1), end, f"last month, same days (1 → {end.day}/{m})")
    if kind == "month":
        y, m = _add_months(w.start.year, w.start.month, -1)
        return Window(dt.date(y, m, 1), _month_end(y, m), f"month {m}/{y}")
    if kind == "quarter":
        y, m = _add_months(w.start.year, w.start.month, -3)
        return Window(dt.date(y, m, 1), _month_end(y, m + 2), f"quarter {(m + 2) // 3}/{y}")
    if kind == "year":
        y = w.start.year - 1
        return Window(dt.date(y, 1, 1), dt.date(y, 12, 31), f"year {y}")
    if kind == "year_to_date":
        # Assumption (rulebook is silent): same span of last year
        end = as_of.replace(year=as_of.year - 1) if (as_of.month, as_of.day) != (2, 29) else dt.date(as_of.year - 1, 2, 28)
        return Window(dt.date(as_of.year - 1, 1, 1), end, "last year, same span")
    # Everything else (days, weeks, last N days, range, Tết): the same number of days right before.
    # this_week: assumption (rulebook is silent), same weekdays of last week.
    # tet: §2 "so với trước Tết" = the same number of days right before 23 tháng Chạp.
    n = (w.end - w.start).days + 1
    return Window(w.start - n * day, w.start - day, f"the {n} days before")
