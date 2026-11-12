"""Stage 8 of write_code (fixed code, no LLM): derived columns, then the schema's finalize / validate / write.
explore_agent.md "Stage 8". Agreed with the user (2026-11-13): every part as the reference converters do it.

1. product identity (rulebook §8 "File có mã thì theo mã", §3 spellings of one name are one product):
   product_key = the product code; a line without a code: when a products table is joined on the line's name
   column, product_name = the joined row's spelling (as common.match_names; stage 3 already matched the row
   accent-insensitively), else the most frequent spelling among the shop's spellings that differ only in accents
   (common.canonical_names); product_key = norm_text of that name. A line with a code keeps its own name.
2. customer_key = the customer code (blank = walk-in, no key) when the shop has codes, else
   parse.customer_name_key(name) (phone suffix stripped, walk-in aliases -> None).
3. line_discount when no column holds it: qty x unit_price - line_amount (common.implied_discount).
4. schema.finalize / validate; manifest: data_end from the README ('Dữ liệu có đến hết dd/mm/yyyy', as the
   references), else the last order_date with a warning; no brief (hand-written per shop in the references);
   sources = line tables, joined tables, conversion tables, README.md.
"""
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src.datamate import schema
from src.datamate.analyst_agent.explore_agent.state import SalesLinePlan
from src.datamate.analyst_agent.explore_agent.v2.write_code.joins import Joined, through
from src.datamate.analyst_agent.explore_agent.v2.write_code.tables import find_column, is_line_table, ref_key
from src.datamate.parse import customer_name_key, fold_accents, is_blank, norm_text
from src.datamate.reference.common import canonical_names, implied_discount, readme_data_end


@dataclass
class FinishReport:
    product_key: dict = field(default_factory=dict)  # how many lines got their key from: code / table / spelling
    names_changed: int = 0  # lines whose product_name became the products table's or the most frequent spelling
    customer_key: str = ""  # 'code' / 'name' / 'none'
    implied_discount: bool = False
    data_end: str = ""
    data_end_from: str = ""  # 'README' / 'last order_date (README phrase not found)'
    sources: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # schema.validate


@dataclass
class Finished:
    lines: pd.DataFrame  # finalized: schema columns and types
    manifest_args: dict  # shop-independent manifest inputs: sources, data_end, brief
    report: FinishReport


def name_join(plan: SalesLinePlan) -> tuple[str, str] | None:
    """(joined table key, its name column) of a products table joined on the line's product_name column."""
    src = next((c for c in plan.columns if c.schema_column == "product_name"
                and is_line_table(plan, c.file, c.sheet)), None)
    if src is None:
        return None
    for j in plan.joins:
        if is_line_table(plan, j.on_file, j.on_sheet) and fold_accents(j.on_key) == fold_accents(src.raw_column):
            # a code + name pair (attach.PAIRS: code first) joins with key = code, alt_key = name
            return ref_key((j.file, j.sheet)), (j.alt_key or j.key)
    return None


def product_identity(lines: pd.DataFrame, plan: SalesLinePlan, joined: Joined, report: FinishReport) -> None:
    idx = lines.index
    code = lines["product_code"] if "product_code" in lines.columns else pd.Series(None, index=idx, dtype=object)
    name = lines["product_name"] if "product_name" in lines.columns else pd.Series(None, index=idx, dtype=object)
    has_code = code.map(lambda v: not is_blank(v))
    no_code = ~has_code & name.map(lambda v: not is_blank(v))
    new_name = name.copy()
    from_table = pd.Series(False, index=idx)
    nj = name_join(plan)
    if nj is not None and no_code.any():
        key, col = nj
        table_name = through(joined, key, find_column(joined.tables[key], col))
        from_table = no_code & table_name.map(lambda v: not is_blank(v))
        new_name[from_table] = table_name[from_table].map(lambda v: str(v).strip())
    rest = no_code & ~from_table
    if rest.any():
        new_name[rest] = canonical_names(name[rest])
    changed = no_code & (new_name.astype(str) != name.astype(str))
    report.names_changed = int(changed.sum())
    lines["product_name"] = new_name
    lines["product_key"] = code.where(has_code, new_name.map(norm_text).where(no_code))
    report.product_key = {"code": int(has_code.sum()), "products table name": int(from_table.sum()),
                          "most frequent spelling": int(rest.sum()),
                          "none": int((~has_code & ~no_code).sum())}


def customer_identity(lines: pd.DataFrame, report: FinishReport) -> None:
    if "customer_code" in lines.columns and lines["customer_code"].notna().any():
        lines["customer_key"] = lines["customer_code"]  # §3: one code = one customer; blank = walk-in
        report.customer_key = "code"
    elif "customer_name" in lines.columns:
        lines["customer_key"] = lines["customer_name"].map(customer_name_key)
        report.customer_key = "name"
    else:
        report.customer_key = "none"


def sources_of(plan: SalesLinePlan) -> list[str]:
    files = [r.file for r in plan.line_tables] + [j.file for j in plan.joins] \
        + [c.table.file for c in plan.conversions if c.table is not None] + ["README.md"]
    return list(dict.fromkeys(files))


def finish(lines: pd.DataFrame, plan: SalesLinePlan, joined: Joined, shop_dir: Path) -> Finished:
    report = FinishReport()
    lines = lines.copy()
    product_identity(lines, plan, joined, report)
    customer_identity(lines, report)
    if "line_discount" not in lines.columns and {"qty", "unit_price", "line_amount"} <= set(lines.columns):
        lines["line_discount"] = implied_discount(pd.to_numeric(lines["qty"]), pd.to_numeric(lines["unit_price"]),
                                                  pd.to_numeric(lines["line_amount"]))
        report.implied_discount = True
    out = schema.finalize(lines)
    report.problems = schema.validate(out)
    try:
        report.data_end, report.data_end_from = readme_data_end(Path(shop_dir) / "README.md"), "README"
    except (TypeError, OSError):  # the phrase is not there (re.search returned None) or no README
        report.data_end = str(out["order_date"].max().date()) if out["order_date"].notna().any() else ""
        report.data_end_from = "last order_date (README phrase not found)"
    report.sources = sources_of(plan)
    return Finished(out, {"sources": report.sources, "data_end": report.data_end, "brief": None}, report)


def write(finished: Finished, out_dir: Path, shop: str) -> dict:
    return schema.write(finished.lines, out_dir, shop, **finished.manifest_args)
