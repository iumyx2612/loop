"""Step 8a, unmapped (code, no LLM; agreed with the user, 2026-11-11): the plan's `unmapped` list, what the plan
leaves out, so a plan judged by hand shows at a glance what was not used. The converter does not read it.

- The line tables and every attached table: each column no step used (not mapped by step 2, not filled by the
  snapshot, not a join column on either side, not forward filled) gives one entry
  '<table> / <column>: <README meaning, else up to SAMPLES distinct values>'.
- Every other table group (never attached: debt, stock, imports, ...): one entry
  '<table>: not attached (<rows> rows; columns: ...)'.
"""
from pathlib import Path

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.analyst_agent.explore_agent.readme import readme_columns
from src.datamate.parse import is_blank

SAMPLES = 3  # distinct values shown for a column without a README meaning
SAMPLE_LEN = 30  # characters per value shown
MAX_COLUMNS = 12  # column names listed for a table that never attached


def _short(v) -> str:
    s = explore._cell(v)
    return s if len(s) <= SAMPLE_LEN else s[:SAMPLE_LEN - 1] + "…"


def what_it_holds(shop_dir: Path, g: explore.TableGroup, column: str) -> str:
    """The README's meaning of the column, else up to SAMPLES distinct values of the group's data rows."""
    for t in g.tables:
        spec = readme_columns(shop_dir, t.file, t.sheet)
        if spec and (m := next((v for k, v in spec.meanings.items() if k.strip().lower() == column.strip().lower()),
                               None)):
            return m.strip()
    seen: list[str] = []
    for t in g.tables:
        j = explore.column_index(t, column)
        if j is None:
            continue
        for r in t.data_rows():
            v = r[j] if j < len(r) else None
            if not is_blank(v) and (s := _short(v)) not in seen:
                seen.append(s)
                if len(seen) == SAMPLES:
                    return "e.g. " + ", ".join(seen)
    return "e.g. " + ", ".join(seen) if seen else "always blank"


def unmapped(shop_dir: Path, groups: dict[str, explore.TableGroup], used: dict[str, set[str]]) -> list[str]:
    """`groups`: every table group by name; `used`: group name -> the columns used, for the line group and every
    attached group (a group not in `used` never attached)."""
    out = []
    for name, g in groups.items():
        if name not in used:
            cols = g.first.columns
            more = f" +{len(cols) - MAX_COLUMNS} more" if len(cols) > MAX_COLUMNS else ""
            rows = sum(len(t.data_numbers()) for t in g.tables)
            out.append(f"{name}: not attached ({rows:,} rows; columns: {', '.join(cols[:MAX_COLUMNS])}{more})")
            continue
        taken = {c.strip().lower() for c in used[name]}
        for col in g.first.columns:
            if col and col.strip().lower() not in taken:
                out.append(f"{name} / {col}: {what_it_holds(shop_dir, g, col)}")
    return out
