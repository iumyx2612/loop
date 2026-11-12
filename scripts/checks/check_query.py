"""Hand-written agent plans for train questions of every sales_lines intent, scored against the answer key.
Each case is exactly what the agent should do: information_finder calls (results r1, r2, ... in order) and one
calculate expression over them. Checks periods.py, query.py, finder.py and calc.py together, no LLM.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_query.py [-v]   (-v prints every finder output)
"""
import datetime as dt
import json
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.query_agent.calc import CalcError, calculate  # noqa: E402
from src.datamate.query_agent.finder import information_finder  # noqa: E402
from src.datamate.parse import fold_accents, norm_text  # noqa: E402
from src.datamate.query_agent.periods import LunarCalendar  # noqa: E402
from src.datamate.query_agent.query import Shop  # noqa: E402

DATA = ROOT / "dataset" / "zaloAI" / "train"
LUNAR = LunarCalendar(DATA / "calendar.csv")


@cache
def shop(shop_id: str) -> Shop:
    return Shop.load(ROOT / "data_std" / "train" / shop_id)


def product(shop_id: str, text: str) -> str:
    """Stand-in for find_entity: the one product whose name contains `text` (accent-insensitive)."""
    t = shop(shop_id).lines[["product_key", "product_name"]].drop_duplicates()
    hits = t[t["product_name"].map(fold_accents).str.contains(fold_accents(text), regex=False)]["product_key"].unique()
    assert len(hits) == 1, f"{shop_id} {text!r}: {len(hits)} products match"
    return hits[0]


def F(measure, period, filters=None, group_by=None, compare=None, top_n=None):
    """One information_finder call, as the agent would write it."""
    return dict(measure=measure, period=period, filters=filters, group_by=group_by, compare=compare, top_n=top_n)


def M(m, y):
    return {"kind": "month", "month": m, "year": y}


def R(a, b):
    return {"kind": "range", "from": a, "to": b}


LAST_WEEK, THIS_WEEK, TODAY, YESTERDAY = {"kind": "last_week"}, {"kind": "this_week"}, {"kind": "today"}, {"kind": "yesterday"}


def P(shop_id, text):
    return {"product_keys": [product(shop_id, text)]}


# (shop, as_of, question, [finder calls → r1, r2, ...], calculate expression)
CASES = [
    ("biz_117", "2025-07-06", "dthu tuan truoc cua cua hang la bnhiu", [F("revenue", LAST_WEEK)], "r1"),
    ("biz_117", "2025-04-25", "doanh thu 30 ngày gần nhất của cửa hàng là bao nhiêu", [F("revenue", {"kind": "last_n_days", "n": 30})], "r1"),
    ("biz_117", "2025-09-11", "doanh thu tuần rồi của cửa hàng là bao nhiêu hả", [F("revenue", LAST_WEEK)], "r1"),
    ("biz_117", "2025-02-28", "tuan nay dthu duoc bao nhiu", [F("revenue", THIS_WEEK)], "r1"),
    ("biz_122", "2026-05-29", "cho toi biet doanh thu tuan nay", [F("revenue", THIS_WEEK)], "r1"),
    ("biz_128", "2026-08-11", "tổng dthu trong ngày hôm nay là bao nhiêu vaayj", [F("revenue", TODAY)], "r1"),
    ("biz_128", "2026-07-08", "check gium dthu hqua cua cua hang la bn", [F("revenue", YESTERDAY)], "r1"),
    ("biz_108", "2025-07-26", "hqua cua hang ban duoc bao nhiu tien", [F("revenue", YESTERDAY)], "r1"),
    ("biz_116", "2026-07-06", "Doanh thu hôm nay là bao nhiêu?", [F("revenue", TODAY)], "r1"),
    ("biz_128", "2026-09-07", "Cho tôi biết doanh thu tháng 11/2025.", [F("revenue", M(11, 2025))], "r1"),
    ("biz_116", "2026-09-03", "dthu thang 6/2025 bao nhiu", [F("revenue", M(6, 2025))], "r1"),
    ("biz_122", "2025-10-18", "Cho mình hỏi số đơn từ đầu năm tới hết hôm nay là bao nhiêu?", [F("order_count", {"kind": "year_to_date"})], "r1"),
    ("biz_108", "2025-10-21", "xem giup hom qua cua hang co bao nhieu don hang", [F("order_count", YESTERDAY)], "r1"),
    # cancellations
    ("biz_108", "2026-05-29", "Cho hỏi trong tháng 10/2025, cửa hàng có mấy đơn ở trạng thái huỷ?", [F("cancelled_count", M(10, 2025))], "r1"),
    ("biz_128", "2026-09-07", "Đếm số đơn đã huỷ trong tháng 7/2026.", [F("cancelled_count", M(7, 2026))], "r1"),
    ("biz_122", "2026-07-17", "thang 1/2026 ti le don bi huy la bao nhieu %", [F("cancel_rate", M(1, 2026))], "r1"),
    ("biz_128", "2026-09-07", "Tháng 2/2026 tỉ lệ đơn bị huỷ là bao nhiêu phần trăm?", [F("cancel_rate", M(2, 2026))], "r1"),
    # average order value
    ("biz_108", "2026-05-29", "Giá trị trung bình một đơn tháng 10/2025 là bao nhiêu nhỉ?", [F("avg_order_value", M(10, 2025))], "r1"),
    ("biz_116", "2026-09-03", "Trung bình mỗi đơn tháng 10/2025 mang về bao nhiêu doanh thu ạ?", [F("avg_order_value", M(10, 2025))], "r1"),
    ("biz_117", "2025-08-05", "tuần trc doanh thu bình quân mỗi đơn hàng là bao nhiêu", [F("avg_order_value", LAST_WEEK)], "r1"),
    ("biz_122", "2025-02-25", "trung bình mỗi đơn tuần rồi mang về bn doanh thu", [F("avg_order_value", LAST_WEEK)], "r1"),
    # comparisons with the previous period
    ("biz_108", "2026-05-29", "dthu thang 2/2026 hon hay kem th truoc bn %", [F("revenue", M(2, 2026), compare="previous")], "r1.pct_change"),
    ("biz_108", "2026-05-29", "Doanh thu tháng 8/2025 tăng hay giảm bao nhiêu phần trăm so với tháng trước?", [F("revenue", M(8, 2025), compare="previous")], "r1.pct_change"),
    ("biz_108", "2026-05-29", "So với tháng liền trước, doanh thu tháng 4 năm 2026 thay đổi bao nhiêu %?", [F("revenue", M(4, 2026), compare="previous")], "r1.pct_change"),
    ("biz_117", "2026-05-07", "Từ đầu tháng này đến hết hnay, doanh thu hơn hay kém cùng kỳ tháng trước mấy %?", [F("revenue", {"kind": "this_month"}, compare="previous")], "r1.pct_change"),
    # price-increase effect: N days from D vs the N days before (§5)
    ("biz_117", "2026-05-20", "So số bình Gas Petrolimex 12kg bán ra trong 14 ngày kể từ ngày 18/04/2026, ngày tăng giá lên 573.500đ, với 14 ngày ngay trước ngày đó: tăng hay giảm bao nhiêu % nhỉ?",
     [F("qty_base", R("2026-04-18", "2026-05-01"), P("biz_117", "Gas Petrolimex 12kg"), compare="previous")], "r1.pct_change"),
    ("biz_117", "2026-05-20", "Xem giúp Xi măng INSEE Power-S bao 50kg lên giá 105.000đ từ ngày 30/01/2026; so 14 ngày kể từ ngày tăng giá với 14 ngày ngay trước, lượng bán tính theo bao tăng hay giảm bao nhiêu phần trăm?",
     [F("qty_base", R("2026-01-30", "2026-02-12"), P("biz_117", "INSEE Power-S"), compare="previous")], "r1.pct_change"),
    ("biz_122", "2026-07-17", "So số chai Betadine Gargle súc họng chai 125ml bán ra trong 14 ngày kể từ ngày 22/01/2026, ngày tăng giá lên 79.000đ, với 14 ngày ngay trước ngày đó: tăng hay giảm bao nhiêu %?",
     [F("qty_base", R("2026-01-22", "2026-02-04"), P("biz_122", "Betadine Gargle"), compare="previous")], "r1.pct_change"),
    # shifts: afternoon vs morning, the morning is the base (§2)
    ("biz_128", "2026-06-09", "tuần rồi số đơn ca chiều chênh ca sáng bao nhiêu % đó",
     [F("order_count", LAST_WEEK, group_by="shift")], "pct_change(value(r1, 'afternoon'), value(r1, 'morning'))"),
    # one product
    ("biz_122", "2026-07-17", "trong tháng 7 năm 2025 cửa hàng bán đc mấy chai Siro ho Bổ phế Nam Hà Chỉ Khái Lộ chai 125ml",
     [F("qty_base", M(7, 2025), P("biz_122", "Bổ phế Nam Hà Chỉ Khái Lộ"))], "r1"),
    ("biz_122", "2026-07-17", "coi giùm tháng 11/2025 bans Enterogermina 2 tỷ/5ml hộp 20 ống đc bn tieenf",
     [F("revenue", M(11, 2025), P("biz_122", "Enterogermina"))], "r1"),
    ("biz_108", "2026-05-29", "số kg nếp cái hoa vàng bán đươc trong tháng 4/2025 là bn nhir",
     [F("qty_base", M(4, 2025), P("biz_108", "nếp cái hoa vàng"))], "r1"),
    ("biz_117", "2026-05-20", "Ngày 25/02/2026 cửa hàng bán Ống PVC Bình Minh D32 cây 4m giá bao nhiêu một cây?",
     [F("unit_price_base", {"kind": "day", "date": "2026-02-25"}, P("biz_117", "PVC Bình Minh D32"))], "r1"),
    # product group: amount and share of the whole shop
    ("biz_116", "2026-09-03", "thang 11/2025 nhom sat thep ban dc bnhiu tien vay", [F("revenue", M(11, 2025), {"product_group": "sắt thép"})], "r1"),
    ("biz_116", "2026-09-03", "Cho mình hỏi tháng 3/2026 nhóm cát đá chiếm bao nhiêu % doanh thu?",
     [F("revenue", M(3, 2026), {"product_group": "cát đá"}), F("revenue", M(3, 2026))], "share(r1, r2)"),
    ("biz_116", "2026-09-03", "Trong tháng 8/2025, nhóm ống nước đóng góp bao nhiêu phần trăm doanh thu cả cửa hàng?",
     [F("revenue", M(8, 2025), {"product_group": "ống nước"}), F("revenue", M(8, 2025))], "share(r1, r2)"),
    ("biz_108", "2026-05-29", "Tháng 8/2025 nhóm mì gói chiếm bao nhiêu % doanh thu?",
     [F("revenue", M(8, 2025), {"product_group": "mì gói"}), F("revenue", M(8, 2025))], "share(r1, r2)"),
    ("biz_122", "2026-07-17", "Xem giúp doanh thu nhóm hàng dùng ngoài chiếm bao nhiêu phần trăm tổng doanh thu tháng 7 năm 2025?",
     [F("revenue", M(7, 2025), {"product_group": "dùng ngoài"}), F("revenue", M(7, 2025))], "share(r1, r2)"),
    # customer group, channel, seller
    ("biz_128", "2026-09-07", "thang 7/2025, doanh thu tu khach nhom than thiet chiem bao nhieu phan tram tong doanh thu cua cua hang nhi",
     [F("revenue", M(7, 2025), {"customer_group": "thân thiết"}), F("revenue", M(7, 2025))], "share(r1, r2)"),
    ("biz_108", "2026-05-29", "trong tong dthu thang 12/2025, doanh thu cac don ban qua Zalo OA chiem bn phan tram",
     [F("revenue", M(12, 2025), {"channel": "zalo_oa"}), F("revenue", M(12, 2025))], "share(r1, r2)"),
    ("biz_108", "2026-05-29", "dthu các đơn bán tại quầy tháng 1/2026 là bao nhiêu?", [F("revenue", M(1, 2026), {"channel": "store"})], "r1"),
    ("biz_108", "2026-05-29", "coi giùm nhân viên bán giỏi nhất tháng 7/2025 là ai", [F("revenue", M(7, 2025), group_by="seller")], "first(r1.key)"),
    # rankings
    ("biz_108", "2026-05-29", "Doanh thu ngày nào cao nhất trong tháng 8 năm 2025 vậy?", [F("revenue", M(8, 2025), group_by="day")], "first(r1.key)"),
    ("biz_117", "2026-05-20", "thang 5/2025 ngay nao cua hang ban duoc nhiu tien nhat", [F("revenue", M(5, 2025), group_by="day")], "first(r1.key)"),
    ("biz_128", "2026-09-07", "thang 7/2026 ngay nao cua hang ban dc nhiu tien nhat", [F("revenue", M(7, 2026), group_by="day")], "first(r1.key)"),
    ("biz_128", "2026-09-07", "Tháng 8/2025 ai mua hàng nhiều tiền nhất ạ?", [F("revenue", M(8, 2025), group_by="customer")], "first(r1.name)"),
    ("biz_108", "2026-05-29", "tháng 3/2026 khách nào mua nhìu tiền nhất", [F("revenue", M(3, 2026), group_by="customer")], "first(r1.name)"),
    ("biz_128", "2026-09-07", "Top 5 khách hàng mua nhiều tiền nhất tháng 5/2026 là ai?", [F("revenue", M(5, 2026), group_by="customer", top_n=5)], "top(r1.name, 5)"),
    ("biz_122", "2026-07-17", "cho tooi 5 khách mua nhìu tiền nhất thangs 6/2026", [F("revenue", M(6, 2026), group_by="customer", top_n=5)], "top(r1.name, 5)"),
    ("biz_116", "2026-09-03", "cho toi 3 khach mua nhiu tien nhat thang 6/2025", [F("revenue", M(6, 2025), group_by="customer", top_n=3)], "top(r1.name, 3)"),
    # handles + calculate over whole frames
    ("biz_117", "2026-05-20", "Trong tháng 1 năm 2026, số khách mua ít nhất 3 đơn là bao nhiêu?",
     [F("order_count", M(1, 2026), group_by="customer")], "count(r1.value >= 3)"),
    ("biz_116", "2026-09-03", "Giai đoạn từ 29/11/2025 đến hết 05/12/2025 có tổng cộng mấy mặt hàng ế, bán ko được cái nào?",
     [F("qty_base", {"kind": "all_before", "date": "2025-11-29"}, group_by="product"), F("qty_base", R("2025-11-29", "2025-12-05"), group_by="product")],
     "count(setdiff(r1.key, r2.key))"),
    ("biz_116", "2026-09-03", "Giai đoạn từ 15/02/2026 đến hết 21/02/2026, cửa hàng có mấy mặt hàng ế hẳn, không ai mua cái nào nhỉ?",
     [F("qty_base", {"kind": "all_before", "date": "2026-02-15"}, group_by="product"), F("qty_base", R("2026-02-15", "2026-02-21"), group_by="product")],
     "count(setdiff(r1.key, r2.key))"),
    ("biz_117", "2026-05-20", "Trong tháng 12/2025, bao nhiêu đơn có tiền hàng đạt 3.100.000 đồng trở lên?",
     [F("revenue", M(12, 2025), group_by="order")], "count(r1.value >= 3100000)"),
    ("biz_117", "2026-05-20", "Trong tháng 5 năm 2025, số đơn có doanh thu ≥ 1.500.000 đồng là bao nhiêu vậy?",
     [F("revenue", M(5, 2025), group_by="order")], "count(r1.value >= 1500000)"),
    ("biz_122", "2026-07-17", "Doanh thu có tăng liên tục qua 3 tháng 12/2025, 1/2026, 2/2026 không?",
     [F("revenue", R("2025-12-01", "2026-02-28"), group_by="month")], "increasing(r1)"),
]


def same(got, key: dict) -> bool:
    t, want = key["type"], key.get("answer")
    if t in ("number", "percent"):
        return isinstance(got, (int, float)) and not isinstance(got, bool) and abs(float(got) - float(want)) < 0.005
    if t == "string":
        return isinstance(got, str) and norm_text(got) in {norm_text(want)} | {norm_text(a) for a in key.get("alias") or []}
    if t == "list":
        aliases = key.get("alias") or [[] for _ in want]
        return isinstance(got, list) and len(got) == len(want) and all(
            norm_text(g) in {norm_text(w)} | {norm_text(a) for a in al} for g, w, al in zip(got, want, aliases))
    return got == want


def run_case(shop_id, as_of, calls, expr, verbose):
    results, texts = {}, []
    for i, call in enumerate(calls, start=1):
        out = information_finder(shop(shop_id), dt.date.fromisoformat(as_of), LUNAR, f"r{i}", **call)
        texts.append(out.text)
        if out.handle is None:
            return out.text, texts
        results[out.handle] = out.frame
    try:
        return calculate(expr, results), texts
    except CalcError as e:
        return f"CALC ERROR: {e}", texts


def main(verbose: bool):
    questions = {(q["business_id"], q["as_of_date"], q["question"]): q
                 for q in map(json.loads, open(DATA / "questions.jsonl", encoding="utf-8"))}
    key = {k["id"]: k for k in map(json.loads, open(DATA / "answers.jsonl", encoding="utf-8"))}
    ok = 0
    for shop_id, as_of, text, calls, expr in CASES:
        q = questions[(shop_id, as_of, text)]
        k = key[q["id"]]
        got, texts = run_case(shop_id, as_of, calls, expr, verbose)
        good = same(got, k)
        ok += good
        if verbose or not good:
            print(f"{'ok  ' if good else 'MISS'} {shop_id} {q['id']} | {text}\n     calculate({expr}) = {got!r}   key = {k.get('answer')!r}")
            if verbose:
                for t in texts:
                    print("     │ " + t.replace("\n", "\n     │ "))
    print(f"{ok}/{len(CASES)} ok")


if __name__ == "__main__":
    main("-v" in sys.argv)
