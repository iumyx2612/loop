"""Step 7, the candidates (code, no LLM; agreed with the user, 2026-11-10): which products of the line tables the
LLM is asked about (item / revenue but not an item / not revenue).

Key per line (agreed): step 2's `product_code` when mapped, else `product_name`: one key per item, 50-170 per shop.
Name shown (agreed): the line's own name (the most common spelling of a code); for a shop writing only the code on
the line, the products table's name, through attach's `product_code` link and the column snapshot filled
`product_name` from; else the key itself.

Selection (agreed: a union of two lists, no weights; measured on 11 shops):
- TOP_K keys by order share (the share of orders holding the key; the share of rows when there is no order id).
  A fee or voucher is written on many bills: `Phí phục vụ` ranks #1-#2 in five restaurants, #11 in biz_128;
  dev_013's `Voucher` #1, `Phí ship` #2.
- every key whose lines are mostly unusual (>= UNUSUAL of its lines with a blank qty, a blank unit price or a
  negative amount; a sign counts only when step 2 mapped that column): dev_013's voucher / shipping / points rows
  are at 100 %, every real product of the 11 shops at <= 1 %. Catches rare ones (`Dùng 500 điểm`, #83 by share).
  At most UNUSUAL_CAP of them, the most common first.
Rejected (measured): price variation (beer / water by can or case vary more than the fee), once-per-order (dishes
are once per order too). Missed by design: a rare row that looks normal.

Candidates whose names differ only in digits are one question (`Dùng 200 điểm`, `Dùng 300 điểm` → `Dùng # điểm`),
grouped among the candidates only (grouping all names first would merge products like `Tã size M 76` /
`Tã size L 68` and change their shares), and only by a real name, never by a bare code (`SP0056` / `SP0061`).
"""
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.v2.column_mapping.nodes import line_tables_of
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import shop_groups, value_key
from src.datamate.analyst_agent.explore_agent.v2.state import WorkflowState
from src.datamate.parse import is_blank, parse_money, parse_qty

TOP_K = 15  # keys taken by order share (biz_128's fee is #11)
UNUSUAL = 0.5  # share of a key's lines with an unusual-number sign
UNUSUAL_CAP = 15  # unusual keys taken at most
EXAMPLES = 2  # example rows per candidate
DIGITS = re.compile(r"\d+")
# the line columns shown in the example rows, in this order (a column mapped to two fields is shown once)
SHOWN = ("order_id", "product_code", "product_name", "qty", "unit", "unit_price", "line_discount", "line_amount")


@dataclass
class Key:
    key: str  # the cell as written (first spelling seen)
    lines: int = 0
    orders: set = field(default_factory=set)
    unusual: int = 0  # lines with a blank qty / blank price / negative amount
    names: Counter = field(default_factory=Counter)  # the line's product names (spellings) for this key
    rows: list = field(default_factory=list)  # (table, row number): the first line, then the last one seen


@dataclass
class Candidate:
    pattern: str  # the question's label: the name, or its digits as '#' when several names share it
    keys: list[str]  # the keys it stands for
    names: list[str]  # every product-name spelling of those keys (the plan's names)
    lines: int
    share: float  # of orders (rows when there is no order id), all its keys together
    unusual: float  # share of its lines with an unusual-number sign
    why: list[str]  # "top share" / "unusual numbers"
    rows: list  # (table, row number) example rows


@dataclass
class Candidates:
    key_col: str | None
    name_source: str  # "line", "products table <name>", "key"
    keys: int = 0  # distinct keys
    total: int = 0  # orders (or rows) holding any key
    tables: list = field(default_factory=list)  # the line tables
    shown: list = field(default_factory=list)  # (label, column) of the example grid
    extra_name: dict = field(default_factory=dict)  # value_key(code) -> name from the products table
    candidates: list[Candidate] = field(default_factory=list)
    skipped: str | None = None


def _num(f, v):
    """f(v)'s number; None for a blank or a cell that does not read."""
    if is_blank(v):
        return None
    try:
        x = f(v)
    except ValueError:
        return None
    return x[0] if isinstance(x, tuple) else x


def products_table_names(state: WorkflowState) -> tuple[str | None, dict]:
    """For a shop writing only codes on the line: (table name, {value_key(code): name}) from the attached table
    linked by product_code whose snapshot filled product_name; (None, {}) when there is none."""
    snaps = {s["table"]: s for s in state.get("snapshots") or []}
    for a in state.get("attached") or []:
        link = next((k for k in a["links"] if k["anchor"] == "product_code"), None)
        name_col = (snaps.get(a["table"]) or {}).get("filled", {}).get("product_name")
        if link is None or name_col is None:
            continue
        out = {}
        for t in shop_groups(state)[a["table"]].tables:
            jc, jn = explore.column_index(t, link["column"]), explore.column_index(t, name_col)
            if jc is None or jn is None:
                continue
            for r in t.data_rows():
                c, n = (r[jc] if jc < len(r) else None), (r[jn] if jn < len(r) else None)
                if (k := value_key(c)) is not None and not is_blank(n):
                    out.setdefault(k, str(n).strip())
        if out:
            return a["table"], out
    return None, {}


def find_candidates(state: WorkflowState) -> Candidates:
    columns = {c.schema_column: c.raw_column for c in state.get("columns") or []}
    key_col = columns.get("product_code") or columns.get("product_name")
    out = Candidates(key_col, "line" if "product_name" in columns else "key")
    if not state.get("line_tables"):
        out.skipped = "no line tables (step 1 chose none)"
        return out
    if key_col is None:
        out.skipped = "no product column on the line (one row per order or ticket): no line can be a fee"
        return out
    if "product_name" not in columns:
        table, names = products_table_names(state)
        if names:
            out.name_source, out.extra_name = f"products table {table}", names
    out.tables = tables = line_tables_of(Path(state["shop_dir"]), state["line_tables"], state["header_rows"],
                                         state.get("skipped_rows"))
    seen_cols = set()
    for f in SHOWN:
        if (c := columns.get(f)) and c not in seen_cols:
            seen_cols.add(c)
            out.shown.append((f, c))

    keys: dict[str, Key] = {}
    holding = set()
    current = None
    for t in tables:
        j = {f: explore.column_index(t, c) for f, c in columns.items()}
        jk = explore.column_index(t, key_col)

        def cell(r, f):
            i = j.get(f)
            return r[i] if i is not None and i < len(r) else None

        for i in t.data_numbers():
            r = t.rows[i - 1]
            if "order_id" in columns:
                if not is_blank(o := cell(r, "order_id")):
                    current = value_key(o)
            else:
                current = (t.file, t.sheet, i)
            raw = r[jk] if jk is not None and jk < len(r) else None
            if (vk := value_key(raw)) is None or current is None:
                continue
            k = keys.setdefault(vk, Key(str(raw).strip()))
            k.lines += 1
            k.orders.add(current)
            holding.add(current)
            if "product_name" in columns and not is_blank(n := cell(r, "product_name")):
                k.names[str(n).strip()] += 1
            unusual = (("qty" in columns and _num(parse_qty, cell(r, "qty")) is None)
                       or ("unit_price" in columns and _num(parse_money, cell(r, "unit_price")) is None)
                       or ("line_amount" in columns and (_num(parse_money, cell(r, "line_amount")) or 0) < 0))
            k.unusual += unusual
            if len(k.rows) < EXAMPLES:
                k.rows.append((t, i))
            else:
                k.rows[-1] = (t, i)  # keep the first line and the last one seen
    out.keys, out.total = len(keys), len(holding)
    if not keys:
        out.skipped = f"{key_col!r} is blank on every line"
        return out

    share = {vk: len(k.orders) / out.total for vk, k in keys.items()}
    by_share = sorted(keys, key=lambda vk: (-share[vk], -keys[vk].lines))
    why: dict[str, list[str]] = defaultdict(list)
    for vk in by_share[:TOP_K]:
        why[vk].append("top share")
    odd = [vk for vk in sorted(keys, key=lambda vk: -keys[vk].lines) if keys[vk].unusual / keys[vk].lines >= UNUSUAL]
    for vk in odd[:UNUSUAL_CAP]:
        why[vk].append("unusual numbers")

    # group the chosen keys by name with digits as '#' (real names only)
    groups: dict[str, list[str]] = {}
    for vk in sorted(why, key=lambda vk: -share[vk]):
        name = display_name(out, keys[vk])
        real = bool(keys[vk].names) or bool(out.extra_name.get(vk))
        groups.setdefault(DIGITS.sub("#", name) if real else f"key:{vk}", []).append(vk)
    for label, members in groups.items():
        ks = [keys[vk] for vk in members]
        names = [display_name(out, k) for k in ks]
        pattern = label if len(members) > 1 else names[0]
        spellings = list(dict.fromkeys(n for k in ks for n in (k.names or {display_name(out, k): 1})))
        lines = sum(k.lines for k in ks)
        out.candidates.append(Candidate(
            pattern=pattern, keys=[k.key for k in ks], names=spellings, lines=lines,
            share=len(set().union(*(k.orders for k in ks))) / out.total,
            unusual=sum(k.unusual for k in ks) / lines,
            why=sorted({w for vk in members for w in why[vk]}),
            rows=(ks[0].rows + [r for k in ks[1:] for r in k.rows])[:EXAMPLES]))
    return out


def display_name(c: Candidates, k: Key) -> str:
    if k.names:
        return k.names.most_common(1)[0][0]
    return c.extra_name.get(value_key(k.key)) or k.key
