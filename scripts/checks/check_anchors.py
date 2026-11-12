"""Read-only check for step 3a (no LLM, no .env): which other tables can attach to the sales lines, and through
which column.

For each shop of a saved step-2 run:
1. Line anchors: the anchor fields step 2 mapped (ANCHOR_FIELDS), with their number of distinct values.
2. Per other table group (as in step 3), per line anchor: the table column that best covers the anchor's values:
   coverage = share of the line column's distinct values found in it; unique = distinct / non-blank cells of that
   column in the table (1.0 = one row per value). Columns below SHOW_MIN coverage are not printed.
3. Chaining: tables that match no line anchor, compared with every column of the tables that did attach in 2
   (coverage of that attached column's values by each column of the unmatched table).
Values are compared as text without accents, case-insensitive (biz_116 joins on names); 12.0 counts as "12".

Usage: .venv/Scripts/python.exe -I scripts/checks/check_anchors.py [run folder] [shop ...]
       (default run folder: runs/explore/v2/train/step_2_column_mapping)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.analyst_agent.explore_agent import explore  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import line_tables_of  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.nodes import other_groups  # noqa: E402
from src.datamate.analyst_agent.explore_agent.v2.utils import load_state  # noqa: E402
from src.datamate.parse import fold_accents, is_blank  # noqa: E402

ANCHOR_FIELDS = ("order_id", "product_code", "product_name", "customer_code", "customer_name")
MIN_COVERAGE = 0.5  # plan_check.MIN_JOIN_COVERAGE
SHOW_MIN = 0.2  # near misses are shown too


def key(v) -> str | None:
    if is_blank(v):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return (fold_accents(str(v).strip()) or "").lower() or None


def cells(tables: list[explore.Table], column: str) -> list[str]:
    out = []
    for t in tables:
        j = explore.column_index(t, column)
        if j is None:
            continue
        out += [k for r in t.rows[t.header_row:] if j < len(r) and (k := key(r[j])) is not None]
    return out


def profile(tables: list[explore.Table]) -> dict[str, tuple[set, float, int]]:
    """column -> (distinct keys, unique ratio, non-blank cells)"""
    out = {}
    for c in tables[0].columns:
        vals = cells(tables, c)
        if vals:
            out[c] = (set(vals), len(set(vals)) / len(vals), len(vals))
    return out


def best_matches(wanted: set, prof: dict) -> list[tuple[str, float, float, int]]:
    """(column, coverage, unique, non-blank) sorted by coverage, those >= SHOW_MIN."""
    hits = [(c, len(wanted & s) / len(wanted), u, n) for c, (s, u, n) in prof.items()]
    return sorted([h for h in hits if h[1] >= SHOW_MIN], key=lambda h: -h[1])


def fmt(h) -> str:
    c, cov, u, n = h
    tag = "ATTACHES" if cov >= MIN_COVERAGE and u >= 0.99 else ("repeats" if cov >= MIN_COVERAGE else "low")
    return f"'{c}' coverage {cov:.0%}, unique {u:.0%} of {n:,} cells  [{tag}]"


def check_shop(state: dict) -> None:
    shop_dir = Path(state["shop_dir"])
    lines = line_tables_of(shop_dir, state["line_tables"], state["header_rows"])
    mapped = {c.schema_column: c.raw_column for c in state["columns"]}
    anchors = {f: set(cells(lines, mapped[f])) for f in ANCHOR_FIELDS if f in mapped}
    print(f"## {state['shop_id']}  line anchors: " +
          (", ".join(f"{f} ← '{mapped[f]}' ({len(v):,} distinct)" for f, v in anchors.items()) or "none"))
    groups = other_groups(shop_dir, state["header_rows"], state["line_tables"])
    profiles = {name: profile(g.tables) for name, g in groups}
    attached, unmatched = {}, []
    for name, prof in profiles.items():
        found = False
        print(f"  [{name}]")
        for f, wanted in anchors.items():
            hits = best_matches(wanted, prof) if wanted else []
            for h in hits[:2]:
                print(f"     {f}: {fmt(h)}")
                if h[1] >= MIN_COVERAGE and h[2] >= 0.99:
                    found = True
        if found:
            attached[name] = prof
        else:
            unmatched.append(name)
            print("     (attaches to no line anchor)")
    if unmatched and attached:
        print("  chaining: unmatched tables against the columns of attached tables")
        for name in unmatched:
            for a_name, a_prof in attached.items():
                for a_col, (a_vals, a_u, _) in a_prof.items():
                    if a_u >= 0.99 or len(a_vals) < 2:
                        continue  # the attached table's own key / constants: not a reference to another table
                    for h in best_matches(a_vals, profiles[name])[:1]:
                        if h[1] >= MIN_COVERAGE:
                            print(f"     [{name}] '{h[0]}' ↔ [{a_name}] '{a_col}': {fmt(h)}")
    print()


def main():
    args = sys.argv[1:]
    run = Path(args.pop(0)) if args and not args[0].startswith(("biz_", "dev_")) else \
        ROOT / "runs" / "explore" / "v2" / "train" / "step_2_column_mapping"
    if not run.is_dir():
        raise SystemExit(f"no run folder {run}")
    for f in sorted(run.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        if args and data["shop"] not in args:
            continue
        check_shop(load_state(data["state"]))


if __name__ == "__main__":
    main()
