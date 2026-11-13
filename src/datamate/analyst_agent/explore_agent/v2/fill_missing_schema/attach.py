"""Step 3's attach (plain code, no LLM; explore_agent.md "Step 3: agreed structure", point 3): which tables can be
linked to the sales lines, and through which column. It is also find_anchors' value check (part b).

Input: state["anchor_tables"] (find_anchors: per table group, the column claimed for each anchor; the line's from
step 2). Start: only the lines are attached. Then in rounds, until no table attaches: table T attaches to a table A
attached in an earlier round, by anchor f, when
  1. both have a column claimed for f,
  2. T's column is unique (at least MIN_UNIQUE of its filled cells distinct: one row per order / product /
     customer, so each line finds one row),
  3. T's column covers at least MIN_COVERAGE of A's distinct values (they are the same codes).
Values are compared as text without accents, case-insensitive (name anchors, biz_116), runs of spaces as one
(dev_014 'Cáp  C-L'); 12.0 counts as "12".

Code + name together (agreed with the user 2026-11-06, dev_011 / dev_014): when T claims both columns of a pair in
PAIRS (product_code + product_name, customer_code + customer_name) and attaches by neither alone, the two columns
are tried as one: each row of T gives the keys of both cells, and
  2'. those keys are unique over the two columns together (at least MIN_UNIQUE): an abbreviation that is another
      row's full name would send a line to two rows,
  3'. together they cover at least MIN_COVERAGE of A's distinct values in A's column for either anchor of the pair.
dev_011's `Hàng` holds the `Ghi tắt` abbreviation on most lines and the `Tên hàng` full name on the rest: each
column alone covers about half of the distinct values, both together all of them.
Such a join gives two links, one per anchor, each with "pair_with" = the other column (rows of `table` attach where
to_column equals column or pair_with), so the anchors of both count as linked.

Alias pairs (2026-11-13, _alias_links): a table that attached by nothing, with a claimed name anchor, may attach by
that name column + another column of its own holding a second spelling (dev_011 / dev_014 'Ghi tắt' short forms);
two links, both of the name anchor, each with "pair_with" and "alias": True.

Because condition 3 is measured against an attached table, wrong claims that confirm each other (biz_123's import
tables all claim order_id ← MaPhieuNhap) never attach: none of them shares values with the lines.

Output, state["attached"]: one entry per attached table (the lines first, round 0), {"table", "round", "links"};
a link = {"anchor", "column", "to_table", "to_column", "coverage", "unique"} (+ "pair_with" for code + name
together), i.e. the plan's Join (rows of
`table` attach to rows of `to_table` where column = to_column). A table attaching by several anchors (a product
list by code and by name) has one link per anchor. Claims in state["dropped_claims"] (resolve_anchors: another
table was chosen for that anchor) are not used; resolve_anchors runs attach_tables again with them. The trace record also says, for every table left out, why
each of its claims did not attach.
"""
import time
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.plan_check import MIN_JOIN_COVERAGE
from src.datamate.analyst_agent.explore_agent.utils import since
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import named_groups
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.parse import fold_accents, is_blank

MIN_COVERAGE = MIN_JOIN_COVERAGE  # 0.5, the same rule plan_check uses for a join
MIN_UNIQUE = 0.99  # payments tables are 98-99 % unique on order_id (explore_agent.md: the thin margin); a run can
# set state["min_unique"] (runner flag --min-unique) to test the anchor resolve with them attached
PAIRS = (("product_code", "product_name"), ("customer_code", "customer_name"))  # tried together (docstring)
# Alias pairs (agreed 2026-11-13, user; explore_agent.md step 3 "attach"): a table that attached by nothing, with a
# claimed name anchor, tries each other column of its own rows as a second spelling of that name
NAME_ANCHORS = ("product_name", "customer_name")
ALIAS_NAME_BELOW = 0.9  # user: only when the claimed name alone covers less than this (the name does not explain the lines)
ALIAS_PAIR_MIN = 0.9  # the two columns together cover at least this
ALIAS_OWN_MIN = 0.25  # the partner alone covers at least this: a real second spelling, not a coincidental match


def value_key(v) -> str | None:
    """A cell as a comparable key: text without accents, lower case, runs of spaces as one; None for blanks."""
    if is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return (fold_accents(" ".join(str(v).split())) or "").lower() or None


class _Values:
    """Distinct keys and uniqueness of (table group, column), read once per run."""

    def __init__(self, groups: dict[str, explore.TableGroup]):
        self.groups, self.cache = groups, {}

    def __call__(self, table: str, column: str) -> tuple[set, float, int]:
        """(distinct keys, unique ratio = distinct / filled cells, filled cells)"""
        if (table, column) not in self.cache:
            keys = []
            for t in self.groups[table].tables:
                j = explore.column_index(t, column)
                if j is not None:
                    keys += [k for r in t.data_rows() if j < len(r) and (k := value_key(r[j])) is not None]
            distinct = set(keys)
            self.cache[table, column] = (distinct, len(distinct) / len(keys) if keys else 0.0, len(keys))
        return self.cache[table, column]

    def pair(self, table: str, c1: str, c2: str) -> tuple[set, float, int]:
        """The same for two columns read as one: each row gives the keys of both cells (once when they are equal),
        so a key in two different rows lowers the unique ratio."""
        if (table, c1, c2) not in self.cache:
            keys = []
            for t in self.groups[table].tables:
                js = [j for j in (explore.column_index(t, c1), explore.column_index(t, c2)) if j is not None]
                for r in t.data_rows():
                    keys += {k for j in js if j < len(r) and (k := value_key(r[j])) is not None}
            distinct = set(keys)
            self.cache[table, c1, c2] = (distinct, len(distinct) / len(keys) if keys else 0.0, len(keys))
        return self.cache[table, c1, c2]


def _pairs(anchors: dict[str, str]) -> list[tuple[str, str]]:
    """The PAIRS a table claims both columns of (its anchors after dropped claims)."""
    return [(f1, f2) for f1, f2 in PAIRS if f1 in anchors and f2 in anchors]


def _coverage(a_keys: set, t_keys: set) -> float:
    return len(a_keys & t_keys) / len(a_keys) if a_keys else 0.0


def attach_tables(anchor_tables: list[dict], groups: dict[str, explore.TableGroup], min_unique: float = MIN_UNIQUE,
                  dropped: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(attached, left out). attached: see the module docstring. left out: {"table", "claims": [{"anchor",
    "column", "unique", "best_coverage", "best_with", "why"}]} for every table that never attached.
    dropped: claims resolve_anchors dropped ({"table", "anchor"}): not used to attach; a table left out because of
    them says so."""
    values = _Values(groups)
    gone = {(d["table"], d["anchor"]) for d in dropped or []}
    claimed = anchor_tables
    anchor_tables = [{**e, "anchors": {f: c for f, c in e["anchors"].items() if (e["table"], f) not in gone}}
                     for e in anchor_tables]
    entries = {e["table"]: e for e in anchor_tables}
    line = next(e["table"] for e in anchor_tables if e["line"])
    attached = [{"table": line, "round": 0, "links": []}]
    rnd = 0
    while True:
        rnd += 1
        done = {a["table"] for a in attached}
        new = []
        for e in anchor_tables:
            if e["table"] in done:
                continue
            links = []
            for f, col in e["anchors"].items():
                t_keys, t_unique, _ = values(e["table"], col)
                if t_unique < min_unique or not t_keys:
                    continue
                for a in attached:  # only tables attached in an earlier round
                    a_col = entries[a["table"]]["anchors"].get(f)
                    if a_col is None:
                        continue
                    a_keys = values(a["table"], a_col)[0]
                    coverage = len(a_keys & t_keys) / len(a_keys) if a_keys else 0.0
                    if coverage >= MIN_COVERAGE:
                        links.append({"anchor": f, "column": col, "to_table": a["table"], "to_column": a_col,
                                      "coverage": round(coverage, 4), "unique": round(t_unique, 4)})
                        break  # one link per anchor: the first (earliest attached) table it links to
            links += _pair_links(e, attached, entries, values, min_unique, {k["anchor"] for k in links})
            if not links:
                links = _alias_links(e, attached, entries, values, groups, min_unique)
            if links:
                new.append({"table": e["table"], "round": rnd, "links": links})
        if not new:
            break
        attached += new

    done = {a["table"] for a in attached}
    left_out = []
    for e in claimed:
        if e["table"] in done:
            continue
        claims = []
        for f, col in e["anchors"].items():
            if (e["table"], f) in gone:
                claims.append({"anchor": f, "column": col, "why": "dropped by anchor resolve (another table chosen)"})
                continue
            t_keys, t_unique, filled = values(e["table"], col)
            best, best_with = 0.0, None
            for a in attached:
                a_col = entries[a["table"]]["anchors"].get(f)
                if a_col is None:
                    continue
                a_keys = values(a["table"], a_col)[0]
                cov = len(a_keys & t_keys) / len(a_keys) if a_keys else 0.0
                if cov > best or best_with is None:
                    best, best_with = cov, f"{a['table']} '{a_col}'"
            if best_with is None:
                why = "no attached table has this anchor"
            elif t_unique < min_unique and best >= MIN_COVERAGE:
                why = "the same values, but they repeat (not one row per value)"
            elif t_unique < min_unique:
                why = "repeats and shares few values"
            else:
                why = "shares too few values"
            claims.append({"anchor": f, "column": col, "unique": round(t_unique, 4), "filled": filled,
                           "best_coverage": round(best, 3), "best_with": best_with, "why": why})
        anchors = entries[e["table"]]["anchors"]  # without the dropped claims
        for f1, f2 in _pairs(anchors):  # code + name together: why that did not attach either
            t_keys, t_unique, filled = values.pair(e["table"], anchors[f1], anchors[f2])
            best, best_with = 0.0, None
            for a in attached:
                for af in (f1, f2):
                    a_col = entries[a["table"]]["anchors"].get(af)
                    if a_col is not None and (cov := _coverage(values(a["table"], a_col)[0], t_keys)) >= best:
                        best, best_with = cov, f"{a['table']} '{a_col}'"
            why = ("no attached table has either anchor" if best_with is None else
                   "a key repeats over the two columns" if t_unique < min_unique else "shares too few values")
            claims.append({"anchor": f"{f1} + {f2}", "column": f"{anchors[f1]} + {anchors[f2]}",
                           "unique": round(t_unique, 4), "filled": filled, "best_coverage": round(best, 3),
                           "best_with": best_with, "why": why})
        left_out.append({"table": e["table"], "claims": claims})
    return attached, left_out


def _pair_links(e: dict, attached: list[dict], entries: dict, values: _Values, min_unique: float,
                linked: set) -> list[dict]:
    """Code + name together (module docstring, 2' and 3'), for each pair of e none of whose anchors linked alone:
    two links (one per anchor) to the first attached table either anchor's column of reaches MIN_COVERAGE."""
    out = []
    for f1, f2 in _pairs(e["anchors"]):
        if f1 in linked or f2 in linked:
            continue
        c1, c2 = e["anchors"][f1], e["anchors"][f2]
        if values(e["table"], c1)[1] < min_unique or values(e["table"], c2)[1] < min_unique:
            continue  # each column must still be one row per value on its own
        t_keys, t_unique, _ = values.pair(e["table"], c1, c2)
        if t_unique < min_unique or not t_keys:
            continue
        for a in attached:
            hit = next(((af, a_col, cov) for af in (f1, f2) if (a_col := entries[a["table"]]["anchors"].get(af))
                        is not None and (cov := _coverage(values(a["table"], a_col)[0], t_keys)) >= MIN_COVERAGE),
                       None)
            if hit:
                _, a_col, cov = hit
                out += [{"anchor": f, "column": c, "pair_with": other, "to_table": a["table"], "to_column": a_col,
                         "coverage": round(cov, 4), "unique": round(t_unique, 4)}
                        for f, c, other in ((f1, c1, c2), (f2, c2, c1))]
                break
    return out


def _alias_links(e: dict, attached: list[dict], entries: dict, values: _Values,
                 groups: dict[str, explore.TableGroup], min_unique: float) -> list[dict]:
    """Alias pair (agreed 2026-11-13, user), for a table that attached by nothing: a claimed name anchor f on column
    c, and another column c2 of the same table (the same rows) holding a second spelling of that name. dev_011 /
    dev_014: the line's 'Hàng' writes the 'Ghi tắt' short form on most lines and the 'Tên hàng' full name on the
    rest; the 9B claims only 'Tên hàng' (product_name; 'Cáp C' is no product code), which alone covers ~50 %.
    Conditions, against an attached table holding f: c and c2 each unique and unique together (min_unique); c alone
    covers < ALIAS_NAME_BELOW (user: the name alone does not explain the lines; keeps out stock reports, whose name
    alone covers ~100 % and which resolve_anchors left out on purpose); c2 alone covers >= ALIAS_OWN_MIN (a real
    second spelling, not one coincidental match: biz_120's stock report sits at 0.898 with a code column covering
    0 %); together >= ALIAS_PAIR_MIN. Replayed on the 44 saved 9B states: fires on dev_011 and dev_014 only.
    Two links, both anchor f: the partner c2 first, pair_with = c, so write_plan's Join has key = c2 and
    alt_key = c (the claimed name), and write_code's stage 8 takes the name from alt_key."""
    for f in NAME_ANCHORS:
        c = e["anchors"].get(f)
        if c is None:
            continue
        k1, u1, _ = values(e["table"], c)
        if u1 < min_unique or not k1:
            continue
        for a in attached:
            a_col = entries[a["table"]]["anchors"].get(f)
            if a_col is None:
                continue
            a_keys = values(a["table"], a_col)[0]
            if _coverage(a_keys, k1) >= ALIAS_NAME_BELOW:
                continue
            for c2 in groups[e["table"]].first.columns:
                if not c2 or c2 == c:
                    continue
                k2, u2, _ = values(e["table"], c2)
                if u2 < min_unique or not k2 or _coverage(a_keys, k2) < ALIAS_OWN_MIN:
                    continue
                t_keys, t_unique, _ = values.pair(e["table"], c, c2)
                cov = _coverage(a_keys, t_keys)
                if t_unique < min_unique or cov < ALIAS_PAIR_MIN:
                    continue
                return [{"anchor": f, "column": col, "pair_with": other, "alias": True, "to_table": a["table"],
                         "to_column": a_col, "coverage": round(cov, 4), "unique": round(t_unique, 4)}
                        for col, other in ((c2, c), (c, c2))]
    return []


def shop_groups(state: WorkflowState) -> dict[str, explore.TableGroup]:
    """Every table group of the shop by its name (the names anchor_tables uses), carrying the filter step's rows."""
    return {n: g for n, g, _ in named_groups(Path(state["shop_dir"]), state["header_rows"], state["line_tables"],
                                             state.get("skipped_rows"))}


def attach(state: WorkflowState) -> dict:
    """The parent graph's node (no LLM)."""
    start = time.perf_counter()
    record = {"step": "attach"}
    anchor_tables = state.get("anchor_tables") or []
    if not any(e["line"] for e in anchor_tables):
        record.update(error="no anchor tables (find_anchors did not run or found no line tables)", seconds=since(start))
        return {"attached": [], "trace": [record]}
    groups = shop_groups(state)
    min_unique = state.get("min_unique") or MIN_UNIQUE
    attached, left_out = attach_tables(anchor_tables, groups, min_unique, state.get("dropped_claims"))
    record.update(min_unique=min_unique, attached=attached, left_out=left_out, seconds=since(start))
    return {"attached": attached, "trace": [record]}
