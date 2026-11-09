"""Column lists stated in a shop's README, per file (and sheet).

37 of the 44 shops list their columns in one format:

    ## Cột trong `DanhSachHoaDon.xlsx`              (optionally: , sheet `DanhMuc`)
    - **Mã hóa đơn**: mã đơn

The file name in the heading may be a pattern (`BanHang_T<tháng>_<năm>.xlsx`) or the base name of split files
(`DanhSachHoaDonChiTiet.xlsx` -> `DanhSachHoaDonChiTiet_1.xlsx`, `_2.xlsx`, `_2025.xlsx`).
The other 7 shops (train biz_106, 109, 123, 124; public test dev_003, 006, 014) list no columns: the header row
then has to be found another way (guess in explore._guess_header, or an LLM looking at the first rows).
"""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

HEADING = re.compile(r"^##\s+Cột trong\s+`([^`]+)`(?:\s*,\s*sheet\s+`([^`]+)`)?\s*$")
ITEM = re.compile(r"^\s*-\s+\*\*(.+?)\*\*\s*:\s*(.*)$")


@dataclass
class ReadmeTable:
    file_pattern: str  # as written in the heading
    sheet: str | None  # None: every sheet of the file
    columns: list[str]  # column names in README order
    meanings: dict[str, str]  # column -> the README's description

    def matches(self, file: str, sheet: str | None) -> bool:
        if self.sheet is not None and sheet != self.sheet:
            return False
        return _file_regex(self.file_pattern).fullmatch(file) is not None


def _file_regex(pattern: str) -> re.Pattern:
    """`BanHang_T<tháng>_<năm>.xlsx` -> placeholders match anything; `X.xlsx` also matches split `X_1.xlsx`."""
    stem, dot, ext = pattern.rpartition(".")
    if not dot:
        stem, ext = pattern, ""
    parts = re.split(r"<[^>]+>", stem)
    stem_re = ".+?".join(re.escape(p) for p in parts)
    return re.compile(stem_re + r"(?:_\d+)?" + (re.escape("." + ext) if ext else ""), re.IGNORECASE)


def parse_readme(text: str) -> list[ReadmeTable]:
    tables: list[ReadmeTable] = []
    current: ReadmeTable | None = None
    for line in text.splitlines():
        h = HEADING.match(line.strip())
        if h:
            current = ReadmeTable(h.group(1).strip(), h.group(2).strip() if h.group(2) else None, [], {})
            tables.append(current)
            continue
        if line.startswith("#"):  # any other section ends the column list
            current = None
            continue
        item = ITEM.match(line) if current is not None else None
        if item:
            name = item.group(1).strip()
            current.columns.append(name)
            current.meanings[name] = item.group(2).strip()
    return [t for t in tables if t.columns]


@lru_cache(maxsize=64)
def _cached(path: str, mtime: float) -> tuple[ReadmeTable, ...]:
    return tuple(parse_readme(Path(path).read_text(encoding="utf-8")))


def readme_tables(shop_dir: Path) -> tuple[ReadmeTable, ...]:
    p = shop_dir / "README.md"
    return _cached(str(p), p.stat().st_mtime) if p.exists() else ()


def readme_columns(shop_dir: Path, file: str, sheet: str | None) -> ReadmeTable | None:
    """The README's column list for one table; a heading naming the sheet wins over one naming only the file."""
    found = [t for t in readme_tables(shop_dir) if t.matches(file, sheet)]
    found.sort(key=lambda t: t.sheet is None)
    return found[0] if found else None
