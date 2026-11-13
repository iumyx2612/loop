"""Task tools: one tool per family of question, each doing the whole multi-step computation and returning the
final answer with its type. They reuse run_query / periods / calc, so the numbers are the same as with
information_finder + calculate; the small model only fills a few slots instead of chaining calls.

Every successful output ends with a line `ANSWER_JSON: {"type": ..., "answer": ...}`; finalize can take the
submission from it directly (no LLM step to pick or retype the value).
"""
import datetime as dt
import functools
import json
from typing import Annotated, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field

from src.datamate.query_agent import calc
from src.datamate.query_agent.periods import PeriodError, previous_period, resolve_period
from src.datamate.query_agent.query import NoData, QueryError, run_query
from src.datamate.query_agent.tools import Filters, Period, _lunar, _shop

ANSWER_PREFIX = "ANSWER_JSON: "

Measure = Literal["revenue", "order_count", "avg_order_value", "qty_base", "customer_count", "cancelled_count",
                  "cancel_rate", "unit_price_base", "gross_profit", "day_count"]
GroupBy = Literal["product", "product_group", "customer", "customer_group", "seller", "channel", "payment_method",
                  "order", "day", "week", "month", "shift"]


def _ctx(state):
    return _shop(state["shop_dir"]), dt.date.fromisoformat(state["as_of_date"]), _lunar(state["calendar_path"])


def _filters(f: Filters | None) -> dict | None:
    return f.model_dump(exclude_none=True) if f else None


def _query(state, measure, spec: dict, filters, group_by=None, window=None):
    shop, as_of, lunar = _ctx(state)
    w = window or resolve_period(spec, as_of, lunar)
    res = run_query(shop, measure, w.start, w.end, filters, group_by)
    return res, f"{w.label}: {res.start} → {res.end}"


def _value(res) -> float:
    v = res.frame["value"].iat[0]
    if v != v:
        raise QueryError("no value in this period")
    return float(v)


def _num(x):
    x = calc._scalar(x)
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _answer(lines: list[str], type_: str, answer) -> str:
    return "\n".join(lines + [ANSWER_PREFIX + json.dumps({"type": type_, "answer": answer}, ensure_ascii=False)])


def _guard(fn):
    """Errors and missing data come back as text the agent can act on, like information_finder."""
    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except NoData as e:
            return f"NO DATA: {e}"
        except (PeriodError, QueryError, calc.CalcError, ValueError) as e:
            return f"ERROR: {e}"
    return run


# --- compare_periods -------------------------------------------------------------------------------------------

class ComparePeriodsInput(BaseModel):
    measure: Measure
    period: Period = Field(description="the period asked about (the newer one)")
    against: Period | None = Field(None, description="the period it is compared with; omit for the previous period "
                                   "(previous month / week / quarter / day, same days of last month for this_month, "
                                   "the N days before for last_n_days and range, the days before Tết for tet)")
    result: Literal["pct_change", "diff"] = Field("pct_change", description="pct_change = % change; diff = amount")
    filters: Filters | None = None
    split_by: GroupBy | None = Field(None, description="compare two groups of ONE period instead of two periods, "
                                     "e.g. split_by='shift', group_a='afternoon', group_b='morning'")
    group_a: str | None = Field(None, description="with split_by: the group asked about (the 'new' side)")
    group_b: str | None = Field(None, description="with split_by: the group it is compared with")
    state: Annotated[dict, InjectedState]


@tool("compare_periods", args_schema=ComparePeriodsInput)
@_guard
def compare_periods(measure: str, period: Period, state: dict, against: Period | None = None,
                    result: str = "pct_change", filters: Filters | None = None, split_by: str | None = None,
                    group_a: str | None = None, group_b: str | None = None) -> str:
    """Tăng / giảm bao nhiêu (%) so với kỳ trước, cùng kỳ, một kỳ khác, or group A vs group B (ca chiều vs ca sáng).
    Returns the final answer: pct_change in percent with 2 decimals (negative = lower), or diff (new − old)."""
    f = _filters(filters)
    shop, as_of, lunar = _ctx(state)
    if split_by:
        if not (group_a and group_b):
            raise QueryError("split_by needs group_a and group_b")
        res, label = _query(state, measure, period.spec(), f, split_by)
        new, old = calc.value(res.frame, group_a), calc.value(res.frame, group_b)
        lines = [f"{measure} by {split_by}, {label}", f"{group_a} = {_num(new)}, {group_b} = {_num(old)}"]
    else:
        res, label = _query(state, measure, period.spec(), f)
        cur = resolve_period(period.spec(), as_of, lunar)
        partial_month = cur.end == as_of and cur.start == as_of.replace(day=1) and \
            (period.kind == "this_month" or (as_of + dt.timedelta(days=1)).day != 1)  # month not over yet
        if against is not None and partial_month and against.kind in ("month", "this_month"):
            # rulebook: "tháng này (tới hôm nay)" is compared with the same days of last month, not the whole month
            against = None
            period = Period(kind="this_month")
        if against is None:
            w = previous_period(period.spec(), as_of, lunar)
            prev, plabel = _query(state, measure, {}, f, window=w)
        else:
            prev, plabel = _query(state, measure, against.spec(), f)
            if (prev.start, prev.end) == (res.start, res.end):
                raise QueryError("`against` gives the same dates as `period`; omit `against` to compare with the "
                                 "previous period (same days of last month for this_month)")
        new, old = _value(res), _value(prev)
        lines = [f"{measure}: {label} = {_num(new)}", f"compared with {plabel} = {_num(old)}"]
    if result == "diff":
        out = _num(new - old)
        return _answer(lines + [f"diff = {out}"], "number", out)
    out = calc.pct_change(new, old)
    return _answer(lines + [f"pct_change = {out}"], "percent", out)


# --- best_period -----------------------------------------------------------------------------------------------

class BestPeriodInput(BaseModel):
    measure: Measure
    period: Period = Field(description="the whole span searched, e.g. the month for 'ngày nào trong tháng 8', or a "
                                       "range covering every month named")
    by: Literal["day", "month", "week"]
    pick: Literal["highest", "lowest"] = "highest"
    filters: Filters | None = None
    state: Annotated[dict, InjectedState]


@tool("best_period", args_schema=BestPeriodInput)
@_guard
def best_period(measure: str, period: Period, by: str, state: dict, pick: str = "highest",
                filters: Filters | None = None) -> str:
    """Ngày nào / tháng nào / tuần nào cao nhất (or thấp nhất). Returns the final answer as a date: YYYY-MM-DD for a
    day or a week (its Monday), YYYY-MM for a month."""
    res, label = _query(state, measure, period.spec(), _filters(filters), by)
    frame = res.frame.dropna(subset=["value"])
    if frame.empty:
        raise QueryError("no rows in this period")
    row = frame.iloc[0] if pick == "highest" else frame.sort_values(["value", "key"], ascending=[True, True]).iloc[0]
    preview = ", ".join(f"{k} = {_num(v)}" for k, v in zip(frame["key"].head(5), frame["value"].head(5)))
    return _answer([f"{measure} by {by}, {label}", f"top: {preview}", f"{pick}: {row['key']} = {_num(row['value'])}"],
                   "date", str(row["key"]))


# --- values_by_period ------------------------------------------------------------------------------------------

class ValuesByPeriodInput(BaseModel):
    measure: Measure
    periods: list[Period] = Field(description="the periods in the order the question names them (one period when "
                                              "groups are listed)")
    filters: Filters | None = None
    groups: list[Filters] | None = Field(None, description="several groups of the same period, in the order named, "
                                         "e.g. [{'payment_method': 'ewallet'}, {'payment_method': 'cash'}]")
    trend: Literal["increasing", "decreasing"] | None = Field(None, description="yes/no questions (tăng liên tục, "
                                                              "giảm dần ... phải không): true when every value is "
                                                              "higher (lower) than the one before")
    state: Annotated[dict, InjectedState]


@tool("values_by_period", args_schema=ValuesByPeriodInput)
@_guard
def values_by_period(measure: str, periods: list[Period], state: dict, filters: Filters | None = None,
                     groups: list[Filters] | None = None, trend: str | None = None) -> str:
    """"Lần lượt bao nhiêu" / "mỗi tháng được bao nhiêu" for several named periods, or for several groups of one
    period (ví điện tử và tiền mặt). Returns the final answer as a list of values in the order given."""
    f, vals, lines = _filters(filters), [], [measure]
    if groups:
        for g in groups:
            res, label = _query(state, measure, periods[0].spec(), {**(f or {}), **_filters(g)})
            vals.append(_num(_value(res)))
            lines.append(f"{label}, {_filters(g)} = {vals[-1]}")
        return _answer(lines, "list", vals)
    for p in periods:
        res, label = _query(state, measure, p.spec(), f)
        vals.append(_num(_value(res)))
        lines.append(f"{label} = {vals[-1]}")
    if trend:
        steps = list(zip(vals, vals[1:]))
        ok = bool(steps) and all((b > a) if trend == "increasing" else (b < a) for a, b in steps)
        return _answer(lines + [f"{trend} at every step: {ok}"], "boolean", ok)
    return _answer(lines, "list", vals)


# --- count_where -----------------------------------------------------------------------------------------------

class CountWhereInput(BaseModel):
    measure: Measure = Field(description="the value tested per group, e.g. revenue per day, order_count per customer")
    period: Period
    per: GroupBy = Field(description="what is counted: day, order, customer, product, ...")
    op: Literal[">=", ">", "<=", "<", "=="] = Field(description="từ X trở lên / ít nhất X → >=; trên / hơn / quá X → >")
    threshold: float
    as_percent: bool = Field(False, description="True: the share (%) of all groups in the period that meet it, e.g. "
                                                "'bao nhiêu % khách mua ít nhất 4 đơn'")
    filters: Filters | None = None
    state: Annotated[dict, InjectedState]


@tool("count_where", args_schema=CountWhereInput)
@_guard
def count_where(measure: str, period: Period, per: str, op: str, threshold: float, state: dict,
                as_percent: bool = False, filters: Filters | None = None) -> str:
    """Bao nhiêu ngày / đơn / khách / mặt hàng có <measure> từ X trở lên (or another comparison); or what % of them.
    Returns the final answer: the count, or the percent with 2 decimals."""
    res, label = _query(state, measure, period.spec(), _filters(filters), per)
    v = res.frame["value"]
    hit = {">=": v >= threshold, ">": v > threshold, "<=": v <= threshold, "<": v < threshold, "==": v == threshold}[op]
    n, total = int(hit.sum()), int(v.notna().sum())
    lines = [f"{measure} per {per}, {label}", f"{n} of {total} {per} rows have value {op} {_num(threshold)}"]
    if as_percent:
        out = calc.share(n, total)
        return _answer(lines + [f"share = {out}"], "percent", out)
    return _answer(lines, "number", n)


# --- rank ------------------------------------------------------------------------------------------------------

class RankInput(BaseModel):
    measure: Measure = Field(description="bán chạy / nhiều nhất (mặt hàng) → qty_base; mua nhiều tiền nhất (khách) "
                                         "→ revenue; nhân viên bán giỏi nhất → revenue; most days with an order "
                                         "(khách trung thành, thợ / nhân viên chăm chỉ) → day_count; in the most "
                                         "orders (được gọi / mua trong nhiều đơn nhất) → order_count; khách sộp → "
                                         "avg_order_value with min_orders (rulebook)")
    period: Period
    by: GroupBy = Field(description="product for hàng / mặt hàng / món / sản phẩm nào; product_group only when the "
                                    "question asks which nhóm / loại; customer, seller, ...")
    k: int = Field(1, description="1 for 'nào nhất' (one name); k for top k (a list)")
    pick: Literal["highest", "lowest"] = "highest"
    filters: Filters | None = None
    min_orders: int | None = Field(None, description="only groups with at least this many orders in the period "
                                                     "(the rulebook's minimum for khách sộp)")
    state: Annotated[dict, InjectedState]


@tool("rank", args_schema=RankInput)
@_guard
def rank(measure: str, period: Period, by: str, state: dict, k: int = 1, pick: str = "highest",
         filters: Filters | None = None, min_orders: int | None = None) -> str:
    """Which product / customer / seller / group is highest (or lowest), or the top k. Returns the final answer: one
    name (k = 1) or a list of names in rank order. Sellers are named by their code."""
    res, label = _query(state, measure, period.spec(), _filters(filters), by)
    frame = res.frame.dropna(subset=["value"])
    if min_orders:
        orders, _ = _query(state, "order_count", period.spec(), _filters(filters), by)
        enough = set(orders.frame.loc[orders.frame["value"] >= min_orders, "key"])
        frame = frame[frame["key"].isin(enough)]
    if pick == "lowest":
        frame = frame.iloc[::-1]
    if frame.empty:
        raise QueryError("no rows in this period")
    col = "key" if by == "seller" else "name"
    names = [str(x) for x in frame[col].head(max(k, 1))]
    preview = "\n".join(f"{i + 1}. {n} = {_num(v)}" for i, (n, v) in enumerate(zip(frame[col].head(max(k, 5)),
                                                                                 frame["value"].head(max(k, 5)))))
    lines = [f"{measure} by {by}, {label}", preview]
    return _answer(lines, "string", names[0]) if k <= 1 else _answer(lines, "list", names)


# --- share_of --------------------------------------------------------------------------------------------------

class ShareOfInput(BaseModel):
    measure: Measure
    period: Period
    part: Filters = Field(description="the part, e.g. {'product_group': '...'} or {'channel': 'store'}")
    whole: Filters | None = Field(None, description="the whole; omit for the whole shop")
    state: Annotated[dict, InjectedState]


@tool("share_of", args_schema=ShareOfInput)
@_guard
def share_of(measure: str, period: Period, part: Filters, state: dict, whole: Filters | None = None) -> str:
    """X chiếm bao nhiêu % (nhóm hàng, mặt hàng, kênh, hình thức thanh toán, nhóm khách) of the whole. Returns the
    final answer: percent with 2 decimals."""
    if measure == "cancel_rate":  # a rate is already a share: "đơn huỷ chiếm mấy %" is the cancel rate itself
        res, label = _query(state, measure, period.spec(), {**(_filters(whole) or {}), **(_filters(part) or {})})
        out = round(_value(res), 2)
        return _answer([f"cancel_rate, {label} = {out}"], "percent", out)
    a, label = _query(state, measure, period.spec(), _filters(part))
    b, _ = _query(state, measure, period.spec(), _filters(whole))
    if _value(a) == 0:  # usually a filter value the shop does not have, rarely a real 0
        raise QueryError(f"the part {_filters(part)} has no {measure} in {label}; check the filter value with "
                         f"find_entity, or abstain if the shop does not record it")
    out = calc.share(_value(a), _value(b))
    return _answer([f"{measure}, {label}: part = {_num(_value(a))}, whole = {_num(_value(b))}", f"share = {out}"],
                   "percent", out)


# --- price_change_effect ---------------------------------------------------------------------------------------

class PriceChangeInput(BaseModel):
    product_keys: list[str] = Field(description="keys from find_entity(kind='product')")
    new_price: float = Field(description="the new price the question names (per base unit), e.g. 18000")
    days: int = Field(description="N: compare N days from the price change with the N days just before it")
    change_date: str | None = Field(None, description="YYYY-MM-DD: the day of the price change when the question names "
                                                      "it (từ ngày D ...); omit to find it from the sales")
    state: Annotated[dict, InjectedState]


@tool("price_change_effect", args_schema=PriceChangeInput)
@_guard
def price_change_effect(product_keys: list[str], new_price: float, days: int, state: dict,
                        change_date: str | None = None) -> str:
    """Hiệu ứng tăng giá: "kể từ khi X tăng giá lên P, số lượng bán N ngày đầu so với N ngày ngay trước đó". Finds the
    day D the price first rose to P (rulebook), then compares qty_base of D … D+N−1 with the N days before D.
    Returns the final answer: percent with 2 decimals."""
    shop, as_of, _ = _ctx(state)
    f = {"product_keys": product_keys}
    prices = run_query(shop, "unit_price_base", None, as_of, f, "day").frame.sort_values("key")
    by_day = prices.groupby("key")["value"].max()
    before, day = None, (dt.date.fromisoformat(change_date) if change_date else None)
    for d, p in ([] if day else by_day.items()):
        if before is not None and abs(p - new_price) < 0.5 and p > before:
            day = dt.date.fromisoformat(d)
            break
        before = p
    if day is None:
        raise QueryError(f"no day where the price rose to {_num(new_price)}; prices seen: "
                         + ", ".join(sorted({str(_num(v)) for v in by_day.values})[:10]))
    n = int(days)
    one = dt.timedelta(days=1)
    a = run_query(shop, "qty_base", day, min(day + (n - 1) * one, as_of), f, None)
    b = run_query(shop, "qty_base", day - n * one, day - one, f, None)
    new, old = _value(a), _value(b)
    out = calc.pct_change(new, old)
    return _answer([f"price rose to {_num(new_price)} on {day}" + (" (as the question says)" if change_date else ""),
                    f"qty_base {a.start} → {a.end} = {_num(new)}; {b.start} → {b.end} = {_num(old)}",
                    f"pct_change = {out}"], "percent", out)


# --- unsold_products -------------------------------------------------------------------------------------------

class UnsoldInput(BaseModel):
    period: Period = Field(description="the period with no sale, e.g. last_n_days n=7 for '7 ngày gần nhất'")
    names: bool = Field(False, description="True only when the question asks WHICH products (mặt hàng nào, kể tên, "
                                           "liệt kê); 'bao nhiêu / mấy mặt hàng' is a count: False")
    filters: Filters | None = None
    state: Annotated[dict, InjectedState]


@tool("unsold_products", args_schema=UnsoldInput)
@_guard
def unsold_products(period: Period, state: dict, names: bool = False, filters: Filters | None = None) -> str:
    """Hàng ế: products the shop sold before the period (rulebook: đã từng bán trước kỳ) with no sale in the period.
    Returns the final answer: their number, or their names in code order."""
    shop, as_of, lunar = _ctx(state)
    w = resolve_period(period.spec(), as_of, lunar)
    f = _filters(filters)
    now = run_query(shop, "qty_base", w.start, w.end, f, "product").frame
    sold = set(now.loc[now["value"] > 0, "key"])
    if w.start is None:
        raise QueryError("the period needs a start: products sold before it are compared with it")
    before = run_query(shop, "qty_base", None, w.start - dt.timedelta(days=1), f, "product").frame
    before = before[(before["value"] > 0) & ~before["key"].isin(sold)].sort_values("key")
    found = [str(x) for x in before["name"]]
    lines = [f"period {w.label}: {w.start} → {w.end}; sold before it but not in it: {len(found)}",
             ", ".join(found[:20]) + (" ..." if len(found) > 20 else "")]
    return _answer(lines, "list", found) if names else _answer(lines, "number", len(found))


TASK_TOOLS = {"compare": compare_periods, "best_period": best_period, "values_by_period": values_by_period,
              "count_threshold": count_where, "ranking": rank, "share": share_of,
              "price_change": price_change_effect, "unsold": unsold_products}


def answer_from(text: str) -> dict | None:
    """The {type, answer} a task tool put at the end of its output, if any."""
    for line in reversed(str(text).splitlines()):
        if line.startswith(ANSWER_PREFIX):
            try:
                return json.loads(line[len(ANSWER_PREFIX):])
            except json.JSONDecodeError:
                return None
    return None
