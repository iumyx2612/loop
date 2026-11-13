"""Stage 7 of write_code (fixed code, no LLM): unit, base_unit, unit_factor, qty_base. explore_agent.md "Stage 7".

Every source of plan.conversions is parsed into one lookup (product, big unit) -> factor in base units:
- readme_list: common.readme_conversions (product names, with the base unit);
- table: read with the plan's header / skipped rows; product cells by code or name (source.key); factor cells in
  factor_format: number = base units; nested = a number (base units) or '<n> <unit>' (n of another unit of the same
  product, resolved through it); text = '1 <big unit> = <n> <unit>' parts joined by ';' (the unit on the right is
  the base unit, or another big unit resolved through it);
- readme_text: the rows the explore step read out of the README.
Products are compared by attach's value_key (as plan_check's coverage check); units by norm_text (accents kept:
'bó' and 'bộ' are different units).

Per line (agreed with the user 2026-11-13; the references' four cases):
A. the line has qty_base and a non-zero qty: unit_factor = qty_base / qty (the shop's own record); the lookup only
   cross-checks it (disagreements counted). No unit on the line (C): the unit is the base unit when the factor is
   1, else the product's one big unit with that factor (none or several: unit left null, counted).
B. a unit and no qty_base: 1 when unit = base_unit, else the lookup's factor; a unit that is neither: null factor,
   a counted failure (never guessed as 1).
D. no unit and no qty_base: *Claude's choice, not discussed:* a product with no big unit in any source has only
   one unit, so factor 1 (unit = base_unit when known); a product with big units: null, counted. A shop with no
   qty column at all (dev_009, dev_012) gets no factor (agreed 2026-11-13).
Same code, same product (agreed 2026-11-13): see _line_products.
base_unit = the line's (a joined products table), else the sources' base unit for the product. *As dev_002's
reference:* a product in no source and with no known base unit takes the line's unit as its base unit (factor 1).
qty_base = qty x unit_factor wherever the line has no qty_base of its own.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate.analyst_agent.explore_agent.state import ConversionSource, SalesLinePlan
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.attach import value_key
from src.datamate.analyst_agent.write_code.normalize import code_text
from src.datamate.analyst_agent.write_code.tables import ReadReport, find_column, read_table
from src.datamate.parse import is_blank, norm_text
from src.datamate.reference.common import readme_conversions

TOL = 1e-6
EXAMPLES = 5
_NUM = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*")
_NUM_UNIT = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s+(\S.*?)\s*")
_TEXT_PART = re.compile(r"\s*1\s+(\S.*?)\s*=\s*(\d+(?:[.,]\d+)?)\s+(\S.*?)\s*")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass
class UnitsReport:
    sources: list[str] = field(default_factory=list)  # per source: what was read
    source_failures: list[str] = field(default_factory=list)  # cells / entries that could not be read
    cases: Counter = field(default_factory=Counter)  # lines per case (A, A+C, B, D)
    cross_check: int = 0  # case A lines whose factor differs from the lookup's
    cross_check_examples: list[str] = field(default_factory=list)
    no_unit_for_factor: Counter = field(default_factory=Counter)  # case C: (product, factor) -> lines
    failures: Counter = field(default_factory=Counter)  # (case, product, unit, why) -> lines
    base_from_sources: int = 0  # lines whose base_unit came from a conversion source
    base_from_unit: int = 0  # lines whose base_unit is their own unit (product in no source)
    base_conflicts: list[str] = field(default_factory=list)  # products whose sources name two base units
    same_code_lines: int = 0  # lines matched to a source through their product code's other lines
    same_code_ambiguous: int = 0  # lines left unmatched: their code's lines matched several source products
    no_qty_column: bool = False  # no qty mapped: unit_factor left empty (dev_009, dev_012)


@dataclass
class Unitized:
    lines: pd.DataFrame
    report: UnitsReport


class Lookup:
    """(product key, unit) -> (n, of_unit): n of `of_unit` (None = base units). Product keys: ('code', key) or
    ('name', key). `base` = the base unit a source names for a product."""

    def __init__(self):
        self.raw: dict[tuple, tuple[float, str | None]] = {}
        self.base: dict[tuple, str] = {}
        self.products: set[tuple] = set()

    def add(self, product: tuple, unit, n: float, of_unit, report: UnitsReport, where: str) -> None:
        u = norm_text(unit)
        if u is None:
            report.source_failures.append(f"{where}: no unit")
            return
        self.raw[(product, u)] = (n, norm_text(of_unit) if of_unit is not None else None)
        self.products.add(product)

    def set_base(self, product: tuple, base, report: UnitsReport) -> None:
        b = norm_text(base)
        if b is None:
            return
        old = self.base.setdefault(product, b)
        if old != b and len(report.base_conflicts) < EXAMPLES:
            report.base_conflicts.append(f"{product[1]}: {old!r} / {b!r}")

    def factor(self, product: tuple, unit: str, base: str | None, depth: int = 0) -> float | None:
        """Base units in one `unit` of `product`; None when unknown."""
        if base is not None and unit == base:
            return 1.0
        hit = self.raw.get((product, unit))
        if hit is None or depth > 4:
            return None
        n, of = hit
        if of is None or of == base or of == self.base.get(product):
            return n
        inner = self.factor(product, of, base, depth + 1)
        return None if inner is None else n * inner

    def units_of(self, product: tuple) -> list[str]:
        return [u for (p, u) in self.raw if p == product]


def _product(kind: str, v) -> tuple | None:
    k = value_key(code_text(v) if kind == "code" else v)
    return (kind, k) if k else None


def read_source(shop_dir: Path, plan: SalesLinePlan, c: ConversionSource, lk: Lookup, report: UnitsReport) -> None:
    before = len(lk.raw)
    if c.kind == "readme_list":
        conv = readme_conversions(shop_dir / "README.md")
        for r in conv.itertuples():
            p = _product("name", r.product)
            if p:
                lk.add(p, r.unit, float(r.factor), None, report, f"README list {r.product!r}")
                lk.set_base(p, r.base_unit, report)
        report.sources.append(f"readme_list: {len(lk.raw) - before} conversions")
        return
    if c.kind == "readme_text":
        for r in c.rows:
            p = _product("name", r.product)
            if p:
                lk.add(p, r.unit, float(r.factor), None, report, f"README text {r.product!r}")
                if r.base_unit:
                    lk.set_base(p, r.base_unit, report)
        report.sources.append(f"readme_text: {len(lk.raw) - before} conversions")
        return
    # table
    df = read_table(shop_dir, c.table.file, c.table.sheet, plan, ReadReport())
    pcol, fcol = find_column(df, c.product_column), find_column(df, c.factor_column)
    ucol = find_column(df, c.unit_column) if c.unit_column else None
    bcol = find_column(df, c.base_unit_column) if c.base_unit_column else None
    name = f"{c.table.file} [{c.table.sheet or ''}]"
    for r in df.itertuples(index=False):
        rec = dict(zip(df.columns, r))
        p = _product(c.key or "name", rec[pcol])
        cell = rec[fcol]
        if p is None or is_blank(cell):
            continue
        where = f"{name} row {rec['source_row']}"
        if bcol and not is_blank(rec[bcol]):
            lk.set_base(p, rec[bcol], report)
        if c.factor_format == "text":
            for part in str(cell).split(";"):
                if not part.strip():
                    continue
                m = _TEXT_PART.fullmatch(part)
                if not m:
                    report.source_failures.append(f"{where}: {part!r} does not read as '1 <unit> = <n> <unit>'")
                    continue
                lk.add(p, m[1], _num(m[2]), m[3], report, where)
            continue
        unit = rec[ucol] if ucol else None
        if isinstance(cell, (int, float)) and not isinstance(cell, bool):
            lk.add(p, unit, float(cell), None, report, where)
        elif m := _NUM.fullmatch(str(cell)):
            lk.add(p, unit, _num(m[1]), None, report, where)
        elif c.factor_format == "nested" and (m := _NUM_UNIT.fullmatch(str(cell))):
            lk.add(p, unit, _num(m[1]), m[2], report, where)
        else:
            report.source_failures.append(f"{where}: factor {cell!r} does not read as {c.factor_format}")
    report.sources.append(f"table {name} ({c.factor_format}, by {c.key}): {len(lk.raw) - before} conversions")


def _line_products(lines: pd.DataFrame, lk: Lookup, report: "UnitsReport") -> pd.Series:
    """Each line's product key in the lookup's terms: by code when the code is in it, else by name.
    Same code, same product (agreed 2026-11-13): a line whose name matches no source takes the match of the other
    lines with its product code, when they all matched the same source product (biz_119: '5 mg' vs the README's
    '5mg' on some lines of SP0027). Codes whose lines matched several source products are left alone (counted)."""
    codes = lines["product_code"] if "product_code" in lines.columns else pd.Series(None, index=lines.index)
    names = lines["product_name"] if "product_name" in lines.columns else pd.Series(None, index=lines.index)
    out, line_codes = [], []
    by_code: dict[tuple, set] = {}
    for c, n in zip(codes, names):
        pc, pn = _product("code", c) if not is_blank(c) else None, _product("name", n) if not is_blank(n) else None
        p = pc if pc in lk.products else pn if pn in lk.products else (pc or pn)
        out.append(p)
        line_codes.append(pc)
        if pc is not None and p in lk.products:
            by_code.setdefault(pc, set()).add(p)
    for i, (p, pc) in enumerate(zip(out, line_codes)):
        if p in lk.products or pc is None or pc not in by_code:
            continue
        if len(by_code[pc]) == 1:
            out[i] = next(iter(by_code[pc]))
            report.same_code_lines += 1
        else:
            report.same_code_ambiguous += 1
    return pd.Series(out, index=lines.index, dtype=object)


def apply_units(lines: pd.DataFrame, shop_dir: Path, plan: SalesLinePlan) -> Unitized:
    report = UnitsReport()
    lines = lines.copy()
    lk = Lookup()
    for c in plan.conversions:
        try:
            read_source(Path(shop_dir), plan, c, lk, report)
        except Exception as e:  # noqa: BLE001 - one unreadable source must not stop the shop
            report.source_failures.append(f"{c.kind}: {type(e).__name__}: {e}")

    idx = lines.index
    col = lambda c: lines[c] if c in lines.columns else pd.Series(None, index=idx, dtype=object)  # noqa: E731
    qty = pd.to_numeric(col("qty"), errors="coerce")
    qb = pd.to_numeric(col("qty_base"), errors="coerce")
    units = col("unit").map(norm_text)
    products = _line_products(lines, lk, report)
    line_base = col("base_unit").map(norm_text)
    report.no_qty_column = "qty" not in lines.columns

    unit_out, base_out, factor_out, qb_out = [], [], [], []
    for p, q, b, u, lb in zip(products, qty, qb, units, line_base):
        base = lb if lb is not None else lk.base.get(p)
        if lb is None and base is not None:
            report.base_from_sources += 1
        pname = p[1] if p else None
        f = None
        if pd.notna(b) and pd.notna(q) and q != 0:  # A
            f = b / q
            expected = lk.factor(p, u, base) if (p and u) else None
            if expected is not None and abs(expected - f) > TOL:
                report.cross_check += 1
                if len(report.cross_check_examples) < EXAMPLES:
                    report.cross_check_examples.append(f"{pname} {u!r}: line {f:g}, source {expected:g}")
            if u is None:  # C
                if abs(f - 1) < TOL:
                    u = base
                    if u is None:
                        report.no_unit_for_factor[(pname, f, "no base unit")] += 1
                else:
                    hits = [x for x in lk.units_of(p) if (g := lk.factor(p, x, base)) is not None and abs(g - f) < TOL]
                    if len(hits) == 1:
                        u = hits[0]
                    else:
                        report.no_unit_for_factor[(pname, f, "none" if not hits else f"{len(hits)} units")] += 1
                report.cases["A+C (no unit column)"] += 1
            else:
                report.cases["A (qty_base on the line)"] += 1
            qb_val = b
        elif u is not None:  # B
            report.cases["B (unit, no qty_base)"] += 1
            if base is None and p not in lk.products:
                base = u
                report.base_from_unit += 1
            f = lk.factor(p, u, base) if p else (1.0 if u == base else None)
            if f is None:
                why = "unit not in the sources" if p in lk.products else "product in no source"
                report.failures[("B", pname, u, why)] += 1
            qb_val = q * f if (f is not None and pd.notna(q)) else None
        else:  # D
            report.cases["D (no unit, no qty_base)"] += 1
            if report.no_qty_column:  # no quantity at all: a factor means nothing (agreed 2026-11-13)
                u = base
            elif p in lk.products:
                report.failures[("D", pname, None, "product has big units, line names none")] += 1
            else:
                f = 1.0
                u = base
            qb_val = q * f if (f is not None and pd.notna(q)) else None
        unit_out.append(u)
        base_out.append(base)
        factor_out.append(f)
        qb_out.append(qb_val)

    lines["unit"] = pd.Series(unit_out, index=idx, dtype=object)
    lines["base_unit"] = pd.Series(base_out, index=idx, dtype=object)
    lines["unit_factor"] = pd.Series(factor_out, index=idx, dtype=object)
    lines["qty_base"] = pd.Series(qb_out, index=idx, dtype=object)
    return Unitized(lines, report)
