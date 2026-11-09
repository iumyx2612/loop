"""The calculate tool: a small, safe expression language over numbers and result handles (r1, r2, ...).

Parsed with `ast` and evaluated node by node from a whitelist: no attribute access on Python objects,
no imports, no code. Handles are the frames information_finder stored:
  - an ungrouped result (one value) reads as that number:      r1 / r2 * 100,  r1.pct_change
  - a grouped result reads as a table; r1.<column> is a column: count(r1.value >= 3), first(r1.name)
Functions are listed in FUNCTIONS; their docstrings are what the agent is told.
"""
import ast
import math
import operator

import pandas as pd


class CalcError(ValueError):
    """The expression cannot be evaluated; the message tells the agent why."""


def _series(x, fn):
    if isinstance(x, pd.Series):
        return x
    if isinstance(x, pd.DataFrame):
        return x["value"]
    raise CalcError(f"{fn}() needs a column or a grouped result, got {type(x).__name__}")


def _num(x, fn):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise CalcError(f"{fn}() needs numbers, got {type(x).__name__}")
    return x


def count(x):
    """count(r1) rows of a result; count(r1.value >= 3) rows where the condition holds; count(setdiff(...)) items."""
    if isinstance(x, pd.DataFrame):
        return len(x)
    if isinstance(x, pd.Series):
        return int(x.sum()) if x.dtype == bool else int(x.notna().sum())
    if isinstance(x, (list, set)):
        return len(x)
    raise CalcError(f"count() needs a result, a column or a list, got {type(x).__name__}")


def total(x):
    """total(r1.value) sum of a column."""
    return float(_series(x, "total").sum())


def mean(x):
    """mean(r1.value) average of a column (not rounded)."""
    return float(_series(x, "mean").mean())


def first(x):
    """first(r1.name) / first(r1.key) / first(r1.value): the top row (results are sorted best first, ties by code)."""
    s = _series(x, "first")
    if s.empty:
        raise CalcError("first() of an empty result")
    return s.iat[0]


def top(x, n):
    """top(r1.name, 5) the first n items of a column, as a list."""
    return list(_series(x, "top").iloc[: int(n)])


def value(result, key):
    """value(r1, "afternoon") the value of the row whose key is `key`; 0 if that row is absent."""
    if not isinstance(result, pd.DataFrame) or "key" not in result:
        raise CalcError("value() needs a grouped result")
    hit = result.loc[result["key"].astype(str) == str(key), "value"]
    return float(hit.iat[0]) if len(hit) else 0.0


def where(result, condition):
    """where(r1, r1.value >= 3) the rows of a grouped result that meet the condition."""
    if not isinstance(result, pd.DataFrame) or not isinstance(condition, pd.Series):
        raise CalcError("where() needs a grouped result and a condition on one of its columns")
    return result[condition.reindex(result.index, fill_value=False)].reset_index(drop=True)


def setdiff(a, b):
    """setdiff(r1.key, r2.key) items of a that are not in b, as a list (e.g. products sold before but not in the period)."""
    b = set(_series(b, "setdiff").dropna())
    return sorted(set(_series(a, "setdiff").dropna()) - b)


def intersect(a, b):
    """intersect(r1.key, r2.key) items in both."""
    return sorted(set(_series(a, "intersect").dropna()) & set(_series(b, "intersect").dropna()))


def pct_change(new, old):
    """pct_change(new, old) = (new − old) / old × 100, 2 decimals; negative if lower (§1)."""
    _num(new, "pct_change"), _num(old, "pct_change")
    if old == 0:
        raise CalcError("pct_change() with old = 0 is undefined")
    return round((new - old) / old * 100, 2)


def share(part, whole):
    """share(part, whole) = part / whole × 100, 2 decimals."""
    _num(part, "share"), _num(whole, "share")
    if whole == 0:
        raise CalcError("share() with whole = 0 is undefined")
    return round(part / whole * 100, 2)


def increasing(result):
    """increasing(r1) True if a time-grouped result (day / week / month) rises strictly, in time order."""
    if not isinstance(result, pd.DataFrame) or "key" not in result:
        raise CalcError("increasing() needs a result grouped by day, week or month")
    v = result.sort_values("key")["value"].tolist()
    return all(a < b for a, b in zip(v, v[1:]))


def decreasing(result):
    """decreasing(r1) True if a time-grouped result falls strictly, in time order."""
    if not isinstance(result, pd.DataFrame) or "key" not in result:
        raise CalcError("decreasing() needs a result grouped by day, week or month")
    v = result.sort_values("key")["value"].tolist()
    return all(a > b for a, b in zip(v, v[1:]))


def round_(x, digits=0):
    """round(x, 2) half up; round(x) to a whole number (money is rounded to the đồng)."""
    _num(x, "round")
    f = 10 ** int(digits)
    return math.floor(x * f + 0.5) / f


FUNCTIONS = {f.__name__.rstrip("_"): f for f in (count, total, mean, first, top, value, where, setdiff, intersect,
                                                   pct_change, share, increasing, decreasing, round_)}
FUNCTIONS |= {"min": lambda x: float(_series(x, "min").min()) if isinstance(x, (pd.Series, pd.DataFrame)) else min(x),
              "max": lambda x: float(_series(x, "max").max()) if isinstance(x, (pd.Series, pd.DataFrame)) else max(x),
              "abs": lambda x: abs(_num(x, "abs"))}

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}


def _is_grouped(frame: pd.DataFrame) -> bool:
    return "key" in frame.columns


def _scalar(x):
    """numpy / pandas scalars → plain Python, so results print and serialize cleanly."""
    if hasattr(x, "item") and not isinstance(x, (pd.Series, pd.DataFrame)):
        x = x.item()
    if isinstance(x, float) and x.is_integer() and abs(x) < 2 ** 53:
        return int(x)
    return x


class _Eval:
    def __init__(self, results: dict[str, pd.DataFrame]):
        self.results = results

    def handle(self, name):
        if name not in self.results:
            known = ", ".join(self.results) or "none yet"
            raise CalcError(f"unknown name {name!r}; result handles available: {known}")
        return self.results[name]

    def __call__(self, node):
        if isinstance(node, ast.Expression):
            return self(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
            return node.value
        if isinstance(node, ast.Name):
            frame = self.handle(node.id)
            if _is_grouped(frame):
                return frame
            if frame.empty or pd.isna(frame["value"].iat[0]):
                raise CalcError(f"{node.id} has no value")
            return _scalar(frame["value"].iat[0])
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            frame = self.handle(node.value.id)
            if node.attr not in frame.columns:
                raise CalcError(f"{node.value.id} has no column {node.attr!r}; columns: {', '.join(frame.columns)}")
            col = frame[node.attr]
            return col if _is_grouped(frame) else _scalar(col.iat[0])
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            a, b = self(node.left), self(node.right)
            if isinstance(a, pd.DataFrame) or isinstance(b, pd.DataFrame):
                raise CalcError("arithmetic needs numbers or columns, not whole results; use r1.value")
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and not isinstance(b, pd.Series) and b == 0:
                raise CalcError("division by zero")
            return _BIN[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not self(node.operand)
        if isinstance(node, ast.BoolOp):
            vals = [self(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
            return _CMP[type(node.ops[0])](self(node.left), self(node.comparators[0]))
        if isinstance(node, ast.Compare):  # a < b < c
            vals = [self(node.left)] + [self(c) for c in node.comparators]
            return all(_CMP[type(op)](x, y) for op, x, y in zip(node.ops, vals, vals[1:]))
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self(e) for e in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id not in FUNCTIONS:
                raise CalcError(f"unknown function {node.func.id!r}; available: {', '.join(FUNCTIONS)}")
            if node.keywords:
                raise CalcError("functions take positional arguments only")
            return FUNCTIONS[node.func.id](*[self(a) for a in node.args])
        raise CalcError(f"not allowed in an expression: {ast.unparse(node)!r}")


def calculate(expression: str, results: dict[str, pd.DataFrame]):
    """Evaluate `expression`; returns a number, bool, string or list (never a table)."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error: {e.msg}") from None
    try:
        out = _Eval(results)(tree)
    except CalcError:
        raise
    except (TypeError, ValueError, KeyError, ZeroDivisionError) as e:
        raise CalcError(f"{type(e).__name__}: {e}") from None
    if isinstance(out, pd.DataFrame):
        raise CalcError("the expression gives a whole table; pick a column or wrap it, e.g. count(...), top(r1.name, 5)")
    if isinstance(out, pd.Series):
        return [_scalar(v) for v in out.tolist()]
    if isinstance(out, list):
        return [_scalar(v) for v in out]
    return _scalar(out)
