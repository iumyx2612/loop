"""The filter step's code part (no LLM): repeated header rows, candidate rows, their patterns, and a pattern's regex.

All on one table group (tables sharing one header: month sheets, split files), rows numbered as in the file.
- Repeated header row: at least 2 cells equal their own column name (common.drop_repeated_headers' rule).
- Dense column: filled on at least DENSE of the group's data rows (non-blank, not a repeated header).
- Candidate: a data row with a blank in at least one dense column. A subtotal row is a label + a total, so it is
  blank where every line is filled (product, quantity, amount). No words are known in advance.
- Pattern: candidates grouped by their word cells (a text cell with a run of >= 3 letters), digits replaced by '#':
  'Cộng ngày 01/05/2025', 'Cộng ngày 02/05/2025' -> 'Cộng ngày #/#/#'. A candidate without any word cell is keyed
  by the columns it fills: '(no text: Thành tiền)' (a label-less total) differs from '(no text: Ngày, SL, Thành
  tiền)' (a line with a blank product), so one verdict never covers both.
"""
import re
from dataclasses import dataclass, field

from src.datamate.analyst_agent.explore_agent import explore
from src.datamate.parse import is_blank

DENSE = 0.95  # a column filled on at least this share of the data rows is dense
WORD = re.compile(r"[^\W\d_]{3,}")  # a run of >= 3 letters (any alphabet, accents included)
DIGITS = re.compile(r"\d+")
SPACES = re.compile(r"\s+")
NO_TEXT = "(no text: "

RowId = tuple[int, int]  # (index of the table in the group, row number in its file)


@dataclass
class Pattern:
    key: str
    rows: list[RowId] = field(default_factory=list)
    answer: str | None = None  # the LLM's verdict, set by the node

    @property
    def has_text(self) -> bool:
        return not self.key.startswith(NO_TEXT)


@dataclass
class GroupRows:
    """What the code found in one table group."""
    data_rows: list[RowId]  # non-blank rows below the header, repeated headers excluded
    header_rows: list[RowId]  # repeated header rows inside the data
    dense: list[int]  # column indexes
    patterns: list[Pattern]  # in order of first appearance

    @property
    def candidates(self) -> int:
        return sum(len(p.rows) for p in self.patterns)


def _blank_row(r: tuple) -> bool:
    return all(is_blank(v) for v in r)


def is_repeated_header(t: explore.Table, r: tuple) -> bool:
    hits = sum(isinstance(v, str) and not c.startswith("_col") and v.strip() == c for c, v in zip(t.columns, r))
    return hits >= 2


def word_cells(r: tuple) -> list[str]:
    """The row's text cells holding a word, whitespace collapsed (digits kept)."""
    return [SPACES.sub(" ", v).strip() for v in r if isinstance(v, str) and WORD.search(v)]


def row_text(r: tuple) -> str:
    """The text a pattern regex is matched against: the word cells joined by ' | '."""
    return " | ".join(word_cells(r))


def pattern_key(t: explore.Table, r: tuple) -> str:
    words = word_cells(r)
    if words:
        return DIGITS.sub("#", " | ".join(words))
    filled = [c for c, v in zip(t.columns, r) if not is_blank(v)]
    return NO_TEXT + ", ".join(filled) + ")"


def pattern_regex(key: str) -> str | None:
    """'Cộng ngày #/#/#' -> '^\\s*Cộng\\s+ngày\\s+\\d+/\\d+/\\d+\\s*$' (match it case-insensitively against
    row_text). None for a no-text pattern: it has no label."""
    if key.startswith(NO_TEXT):
        return None
    body = r"\d+".join(re.escape(part) for part in key.split("#"))
    body = re.sub(r"(?:\\ )+", r"\\s+", body)  # re.escape writes a space as '\ '
    return rf"^\s*{body}\s*$"


def find_rows(g: explore.TableGroup) -> GroupRows:
    data, headers = [], []
    for k, t in enumerate(g.tables):
        for i in range(t.header_row + 1, len(t.rows) + 1):
            r = t.rows[i - 1]
            if _blank_row(r):
                continue
            (headers if is_repeated_header(t, r) else data).append((k, i))
    width = len(g.first.columns)
    filled = [0] * width
    for k, i in data:
        for j, v in enumerate(g.tables[k].rows[i - 1][:width]):
            filled[j] += not is_blank(v)
    dense = [j for j in range(width) if data and filled[j] >= DENSE * len(data)]
    patterns: dict[str, Pattern] = {}
    for k, i in data:
        t = g.tables[k]
        r = t.rows[i - 1]
        if any(j >= len(r) or is_blank(r[j]) for j in dense):
            key = pattern_key(t, r)
            patterns.setdefault(key, Pattern(key)).rows.append((k, i))
    return GroupRows(data, headers, dense, list(patterns.values()))


def examples(p: Pattern) -> list[RowId]:
    """Up to 3 rows spread over the pattern (first, middle, last), so a pattern that changes over months shows it."""
    return [p.rows[x] for x in dict.fromkeys([0, len(p.rows) // 2, len(p.rows) - 1])]
