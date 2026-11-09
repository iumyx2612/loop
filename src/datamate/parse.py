"""Parsers for raw cell values found in the shops' files (money, quantity, dates, names, statuses).

Every parser takes one raw cell (str, int, float, datetime or None) and raises ValueError on a value
it does not recognise, so an unknown format stops the conversion instead of becoming a wrong number.
"""
import datetime as dt
import re
import unicodedata

EXCEL_EPOCH = dt.date(1899, 12, 30)


def is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and v != v)


def clean_text(v) -> str | None:
    """A raw cell as text, stripped; None for blanks. Use it wherever a cell becomes a string:
    str(v) on a blank cell (NaN, None) gives the text 'nan' / 'None', which then looks like a real value."""
    if is_blank(v):
        return None
    return str(v).strip()


def norm_text(v) -> str | None:
    """Comparable form of a label: Unicode NFC, lower case, single spaces. Accents are kept:
    two names that differ in a letter or a tone mark are different names (rulebook §3)."""
    if is_blank(v):
        return None
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(v))).strip().lower()


def fold_accents(v) -> str | None:
    """norm_text without diacritics ('Đinh 5 phân' -> 'dinh 5 phan'), for matching product names some
    files write without accents. Not used for customers: there a different tone mark is a different name."""
    s = norm_text(v)
    if s is None:
        return None
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").replace("đ", "d")


def parse_money(v) -> float | None:
    """VND amount: 13,353,000 / 1.016.000đ / 25,000.0000 / 25k / 1tr440 / 16,56tr / 9tr / -53.500đ / 45000."""
    if is_blank(v):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace("đ", "").replace("vnd", "").replace(" ", "")
    sign = -1.0 if s.startswith("-") else 1.0
    s = s.lstrip("-+")
    if m := re.fullmatch(r"(\d+)tr(\d{1,3})", s):  # 1tr440 = 1,440,000
        return sign * (int(m[1]) * 1_000_000 + int(m[2].ljust(3, "0")) * 1000)
    if m := re.fullmatch(r"(\d+(?:[.,]\d+)?)tr", s):  # 9tr, 16,56tr
        return sign * float(m[1].replace(",", ".")) * 1_000_000
    if m := re.fullmatch(r"(\d+(?:[.,]\d+)?)k", s):  # 25k
        return sign * float(m[1].replace(",", ".")) * 1000
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):  # 13,353,000 / 25,000.0000
        return sign * float(s.replace(",", ""))
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):  # 1.016.000
        return sign * float(s.replace(".", "").replace(",", "."))
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return sign * float(s)
    raise ValueError(f"unrecognised money value {v!r}")


def parse_qty(v) -> tuple[float | None, str | None]:
    """Quantity, with the unit when it is written in the cell: '3 chai' -> (3.0, 'chai'),
    '2 xe 4m3' -> (2.0, 'xe 4m3'), 5 -> (5.0, None), '-1' -> (-1.0, None)."""
    if is_blank(v):
        return None, None
    if isinstance(v, (int, float)):
        return float(v), None
    m = re.fullmatch(r"\s*([-+]?\d+(?:[.,]\d+)?)\s*(.*?)\s*", str(v))
    if not m:
        raise ValueError(f"unrecognised quantity {v!r}")
    return float(m[1].replace(",", ".")), (norm_text(m[2]) if m[2] else None)


def parse_date(v, default_year: int | None = None) -> dt.date | None:
    """dd/mm/yyyy, d thg m yyyy, yyyy-mm-dd, Excel serial, datetime; 'd/m' without a year takes
    `default_year` (rulebook §8: the year of the sheet the row is in)."""
    if is_blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)):
        return EXCEL_EPOCH + dt.timedelta(days=int(v))
    s = str(v).strip()
    if m := (re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s.*)?", s) or re.fullmatch(r"(\d{1,2}) thg (\d{1,2}),? (\d{4})", s)):
        return dt.date(int(m[3]), int(m[2]), int(m[1]))
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?", s):
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    if (m := re.fullmatch(r"(\d{1,2})/(\d{1,2})", s)) and default_year:
        return dt.date(default_year, int(m[2]), int(m[1]))
    if re.fullmatch(r"\d{5}", s):  # Excel serial stored as text
        return EXCEL_EPOCH + dt.timedelta(days=int(s))
    raise ValueError(f"unrecognised date {v!r}")


def parse_datetime(v) -> dt.datetime | None:
    if is_blank(v):
        return None
    if isinstance(v, dt.datetime):
        return v
    s = str(v).strip()
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            pass
    raise ValueError(f"unrecognised datetime {v!r}")


# --- people -------------------------------------------------------------------------------------

WALK_IN_NAMES = {"khách lẻ", "kl", "lẻ", "khach le", "khách vãng lai"}

# "Tên - 0912 365 003", "Tên (0933201672)", "Tên 0919331756"
_PHONE_SUFFIX = re.compile(r"\s*(?:-\s*|\(\s*)?\+?\d[\d .]{7,}\d\s*\)?\s*$")


def strip_phone(name):
    if is_blank(name):
        return None
    return _PHONE_SUFFIX.sub("", str(name)).strip()


def customer_name_key(name) -> str | None:
    """Customer identity for shops without customer codes (rulebook §3): names that differ only in
    spelling (case, spacing, phone suffix) are one customer. Walk-in aliases give None (not a customer)."""
    key = norm_text(strip_phone(name))
    return None if key is None or key in WALK_IN_NAMES else key


# --- order status (rulebook §8: exactly one of four; mapping verified against the train key) ----

STATUS_COMPLETED, STATUS_CANCELLED, STATUS_RETURNED, STATUS_IN_DELIVERY = "completed", "cancelled", "returned", "in_delivery"
STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_RETURNED, STATUS_IN_DELIVERY)

_STATUS_WORDS = {
    STATUS_COMPLETED: ["xong", "đã xong", "hoàn thành", "đã hoàn thành", "đã lấy", "thành công", "ok", "đã giao", "completed"],
    STATUS_CANCELLED: ["đã hủy", "hủy", "huỷ", "đã huỷ", "huỷ đơn", "hủy đơn", "đã hủy đơn", "đã huỷ đơn", "khách hủy", "khách huỷ",
                       "không giao được", "cancel", "cancelled"],
    STATUS_RETURNED: ["đổi trả", "trả hàng", "trả lại", "hoàn trả", "hàng trả về", "đã hoàn hàng", "đã trả hàng", "returned",
                      "khách trả", "trả lại hàng", "khách mang trả", "khách trả lại"],
    STATUS_IN_DELIVERY: ["đang giao", "đang giao hàng", "đang giao dịch", "chờ giao", "đang xử lý", "đang gửi", "đang vận chuyển",
                         "shipping", "đang ship", "chưa giao"],
}
STATUS_MAP = {norm_text(w): status for status, words in _STATUS_WORDS.items() for w in words}


def map_status(v) -> str | None:
    key = norm_text(v)
    if key is None:
        return None
    if key not in STATUS_MAP:
        raise ValueError(f"unknown order status {v!r}")
    return STATUS_MAP[key]


# --- channel and payment method (value sets from the survey of the 30 train shops) ---------------

CHANNELS = ("store", "zalo_oa", "phone", "ecommerce")
_CHANNEL_WORDS = {"store": ["store", "tại quầy", "tại tiệm"], "zalo_oa": ["zalo_oa", "zalo oa", "zalo"],
                  "phone": ["phone", "điện thoại"], "ecommerce": ["ecommerce", "sàn tmđt"]}
CHANNEL_MAP = {norm_text(w): c for c, words in _CHANNEL_WORDS.items() for w in words}

PAYMENT_METHODS = ("cash", "transfer", "ewallet", "cod", "debt")
_PAYMENT_WORDS = {
    "cash": ["cash", "tiền mặt"],
    "transfer": ["transfer", "chuyển khoản"],
    "ewallet": ["ewallet", "ví điện tử"],
    "cod": ["cod", "thu hộ (cod)", "thu hộ"],  # §1: COD is its own method, not cash
    "debt": ["debt", "ghi nợ"],
}
PAYMENT_MAP = {norm_text(w): p for p, words in _PAYMENT_WORDS.items() for w in words}


def map_channel(v) -> str | None:
    key = norm_text(v)
    if key is None:
        return None
    if key not in CHANNEL_MAP:
        raise ValueError(f"unknown sales channel {v!r}")
    return CHANNEL_MAP[key]


def map_payment(v) -> str | None:
    key = norm_text(v)
    if key is None:
        return None
    if key not in PAYMENT_MAP:
        raise ValueError(f"unknown payment method {v!r}")
    return PAYMENT_MAP[key]
