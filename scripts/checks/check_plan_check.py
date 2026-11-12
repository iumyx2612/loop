"""Check plan_check without an LLM: a hand-written correct plan must pass, broken copies must fail with the
expected problem.

The correct plan is biz_108's, written from reference/biz_108.py (four files joined by code). The broken copies
are the mistakes an agent would plausibly make: a status word left out, a join on the wrong column, a column
name misspelt, a column neither mapped nor missing, a needless forward fill, a subtotal label that is not in the
data. biz_122 (order fields only on an order's first line) checks that a missing forward fill is caught.

Usage: .venv/Scripts/python.exe -I scripts/checks/check_plan_check.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))  # -I drops the project root from sys.path
sys.stdout.reconfigure(encoding="utf-8")

from src.datamate.analyst_agent.explore_agent.plan_check import check_plan  # noqa: E402
from src.datamate.analyst_agent.explore_agent.state import (  # noqa: E402
    ColumnSource, ConversionRow, ConversionSource, Join, SalesLinePlan, StatusWord, TableRef,
)

DATA = ROOT / "dataset" / "zaloAI"
LINES, ORDERS, PRODUCTS, CUSTOMERS = ("DanhSachHoaDonChiTiet.xlsx", "DanhSachHoaDon.xlsx", "DanhSachSanPham.xlsx",
                                      "DanhSachKhachHang.xlsx")


def col(schema_column: str, file: str, raw: str) -> ColumnSource:
    return ColumnSource(schema_column=schema_column, file=file, raw_column=raw)


def biz_108_plan() -> SalesLinePlan:
    return SalesLinePlan(
        line_tables=[TableRef(file=LINES)],
        columns=[
            col("order_id", LINES, "Mã hóa đơn"), col("product_code", LINES, "Mã hàng"),
            col("unit", LINES, "Đơn vị tính"), col("qty", LINES, "Số lượng"), col("qty_base", LINES, "Số lượng quy đổi"),
            col("unit_price", LINES, "Đơn giá"), col("line_discount", LINES, "Giảm giá"),
            col("line_amount", LINES, "Thành tiền"),
            col("order_date", ORDERS, "Thời gian"), col("order_time", ORDERS, "Thời gian tạo"),
            col("status", ORDERS, "Trạng thái"), col("customer_name", ORDERS, "Khách hàng"),
            col("customer_code", ORDERS, "Mã KH"), col("line_product_name", LINES, "Tên hàng"),
            col("channel", ORDERS, "Kênh bán"), col("seller", ORDERS, "Người bán"),
            col("product_name", PRODUCTS, "Tên hàng"), col("product_group", PRODUCTS, "Nhóm hàng(3 Cấp)"),
            col("base_unit", PRODUCTS, "ĐVT"), col("customer_group", CUSTOMERS, "Nhóm khách hàng"),
        ],
        missing_columns=["payment_method", "cost_price"],
        joins=[
            Join(file=ORDERS, key="Mã hóa đơn", on_file=LINES, on_key="Mã hóa đơn"),
            Join(file=PRODUCTS, key="Mã hàng", on_file=LINES, on_key="Mã hàng"),
            Join(file=CUSTOMERS, key="Mã khách hàng", on_file=ORDERS, on_key="Mã KH"),
        ],
        forward_fill_columns=[],
        statuses=[StatusWord(raw="Hoàn thành", status="completed"), StatusWord(raw="Đã hủy", status="cancelled"),
                  StatusWord(raw="Đã trả hàng", status="returned"), StatusWord(raw="Đang giao hàng", status="in_delivery")],
        conversions=[ConversionSource(kind="readme_list")],
        return_lines=False, service_fee_names=[], non_revenue_names=[], subtotal_labels=[],
        extra_attributes=[], unmapped=[], notes=[],
    )


def broken(plan: SalesLinePlan, **changes) -> SalesLinePlan:
    return plan.model_copy(update=changes)


def main() -> None:
    shop = DATA / "train" / "data" / "biz_108"
    good = biz_108_plan()
    cases = [("correct plan", good, None)]
    cases.append(("status word left out", broken(good, statuses=good.statuses[:3]), "have no status"))
    cases.append(("status word misspelt", broken(good, statuses=[*good.statuses, StatusWord(raw="Huỷ bỏ", status="cancelled")]),
                  "not values of"))
    wrong_join = [*good.joins[:2], Join(file=CUSTOMERS, key="Mã khách hàng", on_file=ORDERS, on_key="Khách hàng")]
    cases.append(("customers joined on the name", broken(good, joins=wrong_join), "values are found"))
    cases.append(("join key not unique", broken(good, joins=[*good.joins[:1],
                  Join(file=PRODUCTS, key="Nhóm hàng(3 Cấp)", on_file=LINES, on_key="Mã hàng"), good.joins[2]]), "not unique"))
    cases.append(("misspelt column", broken(good, columns=[*good.columns[1:], col("order_id", LINES, "Ma hoa don so")]),
                  "has no column"))
    cases.append(("cost_price forgotten", broken(good, missing_columns=["payment_method"]), "not mapped and not in missing_columns"))
    cases.append(("file used but not joined", broken(good, joins=good.joins[:2]), "neither a line table nor joined"))
    cases.append(("needless forward fill", broken(good, forward_fill_columns=["Mã hóa đơn", "Mã hàng"]), "never blank"))
    cases.append(("subtotal label not in the data", broken(good, subtotal_labels=["Tổng cộng ca"]), "is not in any line table"))
    cases.append(("optional column listed as missing (true, harmless)",
                  broken(good, missing_columns=[*good.missing_columns, "line_product_name"]), None))
    cases.append(("computed column listed as missing",
                  broken(good, missing_columns=[*good.missing_columns, "customer_key"]), "computed by the converter"))
    cases.append(("computed column mapped", broken(good, columns=[*good.columns, col("product_key", LINES, "Mã hàng")]),
                  "computed by the converter"))
    # live run 6, biz_116: the product list's cost is today's cost, not the cost on the sale line (§5)
    cases.append(("cost_price from the product list",
                  broken(good, columns=[*good.columns, col("cost_price", PRODUCTS, "Giá bán")],
                         missing_columns=["payment_method"]), "cost_price must be the cost written on the sale line"))

    failed = 0
    for name, plan, expect in cases:
        problems = check_plan(shop, plan)
        ok = not problems if expect is None else any(expect in p for p in problems)
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} biz_108 {name}: {len(problems)} problem(s)")
        if not ok or expect is not None:
            for p in problems[:4]:
                print(f"       - {p[:300]}")

    # biz_122: order code, date, status, customer only on an order's first line
    f122 = "DonHang_ChiTiet_1.xlsx"
    plan122 = SalesLinePlan(
        line_tables=[TableRef(file=f122), TableRef(file="DonHang_ChiTiet_2026.xlsx")],
        columns=[col("order_id", f122, "Mã đơn hàng"), col("order_date", f122, "Ngày đặt hàng")],
        missing_columns=[], joins=[], forward_fill_columns=[], statuses=[], conversions=[],
        return_lines=False, service_fee_names=[], non_revenue_names=[], subtotal_labels=[],
        extra_attributes=[], unmapped=[], notes=[])
    problems = check_plan(DATA / "train" / "data" / "biz_122", plan122)
    ok = any("have a blank 'Mã đơn hàng'" in p for p in problems)
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} biz_122 forward fill not listed")
    for p in problems:
        if "forward_fill" in p:
            print(f"       - {p[:300]}")
    fixed = plan122.model_copy(update={"forward_fill_columns": ["Mã đơn hàng", "Ngày đặt hàng"]})
    ok = not any(p.startswith("forward_fill") for p in check_plan(DATA / "train" / "data" / "biz_122", fixed))
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} biz_122 forward fill listed: no forward-fill problem")

    # biz_117: return lines as negative quantities (105 / 117 / dev_004 shape), lower in the sheets than a first peek
    f117 = "SoBanHang.xlsx"
    plan117 = plan122.model_copy(update={
        "line_tables": [TableRef(file=f117)], "forward_fill_columns": [],
        "columns": [col("qty", f117, "Số lượng hàng"), col("order_date", f117, "Ngày lập")]})
    problems = check_plan(DATA / "train" / "data" / "biz_117", plan117)
    ok = any("return_lines: false, but" in p for p in problems)
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} biz_117 negative quantities with return_lines false")
    for p in problems:
        if p.startswith("return_lines"):
            print(f"       - {p[:300]}")
    problems = check_plan(DATA / "train" / "data" / "biz_117", plan117.model_copy(update={"return_lines": True}))
    ok = not any(p.startswith("return_lines") for p in problems)
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} biz_117 return_lines true: no return_lines problem")
    extra = 4

    def case(name: str, ok: bool, problems: list[str]) -> None:
        nonlocal failed, extra
        extra += 1
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
        for p in problems if not ok else []:
            print(f"       - {p[:300]}")

    # live run 6, biz_122: split line files, one entry per file; 'Đang giao dịch' is only in the 2026 file
    f2026 = "DonHang_ChiTiet_2026.xlsx"
    split = plan122.model_copy(update={
        "forward_fill_columns": ["Mã đơn hàng", "Ngày đặt hàng"],
        "columns": [col("order_id", f122, "Mã đơn hàng"), col("order_id", f2026, "Mã đơn hàng"),
                    col("order_date", f122, "Ngày đặt hàng"), col("status", f122, "Trạng thái đơn hàng")],
        "statuses": [StatusWord(raw="Đã hoàn thành", status="completed"), StatusWord(raw="Đã hủy", status="cancelled"),
                     StatusWord(raw="Đã hoàn hàng", status="returned"),
                     StatusWord(raw="Đang giao dịch", status="in_delivery")]})
    problems = check_plan(DATA / "train" / "data" / "biz_122", split)
    case("biz_122 same column in both split files: one mapping; statuses read over both files",
         not any("mapped" in p or p.startswith("statuses") for p in problems), problems)
    problems = check_plan(DATA / "train" / "data" / "biz_122", split.model_copy(update={
        "columns": [*split.columns, col("order_date", f2026, "Tên khách hàng")]}))
    case("biz_122 one schema column from two different raw columns: duplicate",
         any("order_date is mapped 2 times from different columns" in p for p in problems), problems)

    # live run 6, biz_117: 'hủy' and 'Hủy' are both real values
    words = [("đã lấy", "completed"), ("Đã hoàn thành", "completed"), ("Thành công", "completed"), ("hủy", "cancelled"),
             ("Hủy", "cancelled"), ("trả lại", "returned"), ("Đã trả hàng", "returned"), ("Đang giao", "in_delivery"),
             ("đang gửi", "in_delivery")]
    st117 = plan117.model_copy(update={"return_lines": True,
                                       "columns": [*plan117.columns, col("status", f117, "Ghi chú TT")],
                                       "statuses": [StatusWord(raw=w, status=s) for w, s in words]})
    problems = check_plan(DATA / "train" / "data" / "biz_117", st117)
    case("biz_117 'hủy' and 'Hủy' both listed: no problem", not any(p.startswith("statuses") for p in problems), problems)
    problems = check_plan(DATA / "train" / "data" / "biz_117", st117.model_copy(update={
        "statuses": [*st117.statuses, StatusWord(raw="HỦY", status="completed")]}))
    case("biz_117 one word with two statuses: problem", any("given different statuses" in p for p in problems), problems)
    # live run 7, biz_116: the product list is another sheet of the line workbook; its 'Giá vốn' is not a line cost
    book = "Bao_cao_ban_hang.xlsx"
    plan116 = plan122.model_copy(update={
        "line_tables": [TableRef(file=book, sheet="So_chi_tiet_ban_hang")], "forward_fill_columns": [],
        "columns": [col("order_id", book, "Mã đơn hàng").model_copy(update={"sheet": "So_chi_tiet_ban_hang"}),
                    ColumnSource(schema_column="cost_price", file=book, sheet="Danh sách hàng hóa", raw_column="Giá vốn")],
        "joins": [Join(file=book, sheet="Danh sách hàng hóa", key="Tên hàng hóa", on_file=book,
                       on_sheet="So_chi_tiet_ban_hang", on_key="Tên hàng hóa")]})
    problems = check_plan(DATA / "train" / "data" / "biz_116", plan116)
    case("biz_116 cost_price from the product sheet of the line workbook: problem",
         any("cost_price must be the cost written on the sale line" in p for p in problems), problems)

    # Conversion sources (survey/conversions.md): one correct source per format, then broken copies. Only the
    # conversions problems are looked at (the plans map no columns)
    def conv_problems(split: str, shop_id: str, line_file: str, sources: list[ConversionSource]) -> list[str]:
        plan = plan122.model_copy(update={"line_tables": [TableRef(file=line_file)], "columns": [],
                                          "conversions": sources})
        return [p for p in check_plan(DATA / split / "data" / shop_id, plan) if p.startswith("conversions")]

    def table(file, sheet=None, **kw) -> ConversionSource:
        return ConversionSource(kind="table", table=TableRef(file=file, sheet=sheet), **kw)

    by_name = table("DanhSachQuyDoi.xlsx", key="name", product_column="Tên sản phẩm", unit_column="Đơn vị quy đổi",
                    factor_column="Tỉ lệ quy đổi", factor_format="number", base_unit_column="Đơn vị cơ bản")
    problems = conv_problems("train", "biz_122", f122, [by_name])
    case("biz_122 conversion table by name: no problem", not problems, problems)
    problems = conv_problems("train", "biz_122", f122, [by_name.model_copy(update={"factor_column": "Tên sản phẩm"})])
    case("biz_122 product names as the factor column: problem", any("do not read as" in p for p in problems), problems)
    problems = conv_problems("train", "biz_122", f122, [by_name.model_copy(update={"unit_column": "ĐV lớn"})])
    case("biz_122 misspelt unit column: problem", any("has no column" in p for p in problems), problems)
    problems = conv_problems("train", "biz_122", f122, [ConversionSource(kind="readme_list")])
    case("biz_122 readme_list but the README has no list: problem", any("has no list" in p for p in problems), problems)
    problems = conv_problems("train", "biz_122", f122, [ConversionSource(kind="readme_list", key="name")])
    case("biz_122 table field on a readme_list: problem", any("belong to kind = table" in p for p in problems), problems)

    by_code = table("DuLieuBanHang.xlsx", "DanhSachDonViTinh", key="code", product_column="Mã hàng",
                    unit_column="ĐVT", factor_column="Quy đổi", factor_format="number")
    problems = conv_problems("train", "biz_109", "DanhSachHoaDonChiTiet.xlsx", [by_code])
    case("biz_109 conversion sheet by code: no problem", not problems, problems)

    nested = table("DanhSachQuyDoi.xlsx", key="name", product_column="Tên sản phẩm", unit_column="Đơn vị quy đổi",
                   factor_column="Tỉ lệ quy đổi", factor_format="nested", base_unit_column="Đơn vị cơ bản")
    problems = conv_problems("public_test", "dev_005", "BanHang_Thang01_2025.xlsx", [nested])
    case("dev_005 nested factors ('15 vỉ') as nested: no problem", not problems, problems)
    problems = conv_problems("public_test", "dev_005", "BanHang_Thang01_2025.xlsx",
                             [nested.model_copy(update={"factor_format": "number"})])
    case("dev_005 nested factors as number: problem", any("do not read as" in p for p in problems), problems)

    text = table("QuanLy.xlsx", "DanhMuc", key="name", product_column="Tên hàng", factor_column="Quy đổi",
                 factor_format="text", base_unit_column="ĐVT")
    problems = conv_problems("public_test", "dev_013", "BanHang_2025.xlsx", [text])
    case("dev_013 catalog text column ('1 thùng = 4 bịch; ...'): no problem", not problems, problems)
    problems = conv_problems("public_test", "dev_013", "BanHang_2025.xlsx",
                             [text.model_copy(update={"factor_format": "number", "unit_column": "ĐVT"})])
    case("dev_013 text column as number: problem", any("do not read as" in p for p in problems), problems)

    pipes = ["Ống PVC Bình Minh D32 cây 4m", "Ống PVC Bình Minh D50 cây 4m", "Ống PVC Bình Minh D90 cây 4m"]
    prose = ConversionSource(
        kind="readme_text",
        text="Ống PVC Bình Minh D32 cây 4m, Ống PVC Bình Minh D50 cây 4m, Ống PVC Bình Minh D90 cây 4m bán theo bó, "
             "1 bó = 10 cây",
        rows=[ConversionRow(product=p, unit="bó", factor=10, base_unit="cây") for p in pipes])
    danhmuc = table("DanhMuc.xlsx", key="name", product_column="Hàng", unit_column="Loại ĐV", factor_column="Hệ số",
                    factor_format="number", base_unit_column="ĐV gốc")
    problems = conv_problems("public_test", "dev_004", "SoBanHang.xlsx", [danhmuc, prose])
    case("dev_004 table + README prose: no problem", not problems, problems)
    problems = conv_problems("public_test", "dev_004", "SoBanHang.xlsx", [danhmuc, prose.model_copy(update={
        "rows": [*prose.rows, ConversionRow(product="Ống PVC Bình Minh D114 cây 4m", unit="bó", factor=10)]})])
    case("dev_004 prose row for a product the text does not name: problem",
         any("is not in the quoted text" in p for p in problems), problems)
    problems = conv_problems("public_test", "dev_004", "SoBanHang.xlsx",
                             [danhmuc, prose.model_copy(update={"text": "1 bó = 12 cây"})])
    case("dev_004 prose text not in the README: problem", any("quoted exactly" in p for p in problems), problems)
    print(f"\n{len(cases) + extra - failed}/{len(cases) + extra} ok")


if __name__ == "__main__":
    main()
