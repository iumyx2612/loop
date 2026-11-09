"""The find_entity tool's logic: match the free text of a question ("bans Enterogermina 2 tỷ/5ml hộp 20 ống",
"nhom cat da") against the values the shop actually has, and return the best candidates with their exact keys.
information_finder then filters on those keys, so fuzzy matching never happens inside a query.
"""
import difflib
import re

import pandas as pd

from src.datamate.parse import fold_accents
from src.datamate.query_agent.query import Shop

KINDS = {  # kind -> (key column, columns shown with each candidate)
    "product": ("product_key", ["product_code", "product_name", "product_group", "base_unit"]),
    "product_group": ("product_group", []),
    "customer": ("customer_key", ["customer_name"]),
    "customer_group": ("customer_group", []),
    "seller": ("seller", []),
}
MAX_CANDIDATES = 5


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:[.,/][a-z0-9]+)*", fold_accents(s) or ""))


def _score(query: str, name: str) -> float:
    """1.0 = every word of the name is in the query (questions add words around the name: "bán được mấy chai X");
    otherwise the better of word overlap and character similarity, so typos still match."""
    q, n = _tokens(query), _tokens(name)
    if not n:
        return 0.0
    covered = len(q & n) / len(n)
    similar = difflib.SequenceMatcher(None, fold_accents(query), fold_accents(name)).ratio()
    return round(max(covered, similar), 3)


def candidates(shop: Shop, kind: str, text: str) -> pd.DataFrame:
    """The shop's values of `kind` ranked by similarity to `text`: columns key, <shown columns>, score."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {', '.join(KINDS)}")
    key_col, shown = KINDS[kind]
    if not shop.has(key_col):
        raise LookupError(f"this shop does not record {kind.replace('_', ' ')}s")
    lines = shop.lines[shop.lines[key_col].notna()]
    if kind == "product":
        lines = lines[lines["line_kind"] == "product"]
    table = lines.groupby(key_col)[shown].agg(lambda s: s.mode().iat[0] if s.notna().any() else None) if shown \
        else pd.DataFrame(index=lines[key_col].unique())
    table = table.rename_axis("key").reset_index()
    labels = table[shown[1] if kind == "product" else shown[0]] if shown else table["key"]
    table["score"] = [max(_score(text, str(lbl)), _score(text, str(k))) for lbl, k in zip(labels, table["key"])]
    return table.sort_values(["score", "key"], ascending=[False, True]).head(MAX_CANDIDATES).reset_index(drop=True)
