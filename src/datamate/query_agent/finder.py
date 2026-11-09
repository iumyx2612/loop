"""The information_finder tool's logic, without LangGraph: resolve the period, run the query (twice with
compare), give the full result a handle (r1, r2, ...) and describe it for the agent.

The full frame goes to the caller, which keeps it under its handle (in the graph state, later) so that
`calculate` can use every row; the agent itself only reads the text: shape, what was counted, top rows.
"""
import datetime as dt
from dataclasses import dataclass

import pandas as pd

from src.datamate.query_agent.periods import LunarCalendar, PeriodError, previous_period, resolve_period
from src.datamate.query_agent.query import NoData, QueryError, Shop, compare as compare_results, run_query

PREVIEW_ROWS = 10


@dataclass
class FinderOutput:
    text: str  # what the agent reads
    handle: str | None = None  # None when nothing was stored (error, no data)
    frame: pd.DataFrame | None = None
    no_data: bool = False


def _fmt(v) -> str:
    if isinstance(v, float):
        if v != v:
            return "n/a"
        return f"{int(v)}" if v.is_integer() else f"{v:.2f}"
    return "" if v is None or v is pd.NA else str(v)


def _table(frame: pd.DataFrame, n: int) -> str:
    cols = [c for c in frame.columns if not (c == "code" and frame[c].isna().all())]
    rows = [[_fmt(v) for v in r] for r in frame[cols].head(n).itertuples(index=False)]
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths))]
    lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths)) for r in rows]
    return "\n".join(lines)


def information_finder(shop: Shop, as_of: dt.date, lunar: LunarCalendar | None, handle: str, measure: str,
                       period: dict, filters: dict | None = None, group_by: str | None = None,
                       top_n: int | None = None, compare: str | None = None) -> FinderOutput:
    try:
        w = resolve_period(period, as_of, lunar)
        cur = run_query(shop, measure, w.start, w.end, filters, group_by)
        if compare not in (None, "previous"):
            raise QueryError(f"compare must be 'previous' or omitted, got {compare!r}")
        prev_text = ""
        frame = cur.frame
        if compare == "previous":
            p = previous_period(period, as_of, lunar)
            prev = run_query(shop, measure, p.start, p.end, filters, group_by)
            frame = compare_results(cur, prev, measure)
            prev_text = f"\ncompared with: {p.label}: {prev.start} → {prev.end}" + "".join(f" ({n})" for n in prev.notes)
    except NoData as e:
        return FinderOutput(f"NO DATA: {e}", no_data=True)
    except (PeriodError, QueryError) as e:
        return FinderOutput(f"ERROR: {e}")

    used = {k: v for k, v in (filters or {}).items() if v not in (None, "", [])}
    head = [f"{handle}: {measure}" + (f" by {group_by}" if group_by else ""),
            f"period: {w.label}: {cur.start} → {cur.end}" + prev_text,
            f"rows counted: {cur.rows_used}" + (f"; filters: {used}" if used else "")]
    head += [f"note: {n}" for n in cur.notes]
    if group_by is None and len(frame) > 1:  # unit_price_base: several prices on the same day
        head.append(f"result: {len(frame)} different values")
        head.append(_table(frame, PREVIEW_ROWS))
    elif group_by is None:
        row = frame.iloc[0]
        head.append("result: " + ", ".join(f"{c} = {_fmt(row[c])}" for c in frame.columns))
    else:
        n = top_n or PREVIEW_ROWS
        head.append(f"result: {len(frame)} rows, sorted by value desc (ties: code asc)"
                    + (f"; showing the first {min(n, len(frame))}" if len(frame) > n else ""))
        head.append(_table(frame, n))
    return FinderOutput("\n".join(head), handle, frame)
