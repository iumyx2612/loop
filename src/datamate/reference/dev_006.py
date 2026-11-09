"""dev_006 (pharmacy, POS sqlite): orders in `don_hang`, lines in `chi_tiet_don` (with converted quantity and cost
at sale), products, customers and staff in their own tables, all joined by code. Dates are text in mixed formats.
Questions name staff by full name, so `seller` holds the name from `nhan_vien` (the grader accepts name or code)."""
from pathlib import Path

import pandas as pd

from src.datamate.parse import clean_text, map_channel, map_payment, map_status, norm_text, parse_datetime
from src.datamate.reference.common import Converted, dates, drop_duplicate_lines, money, read_sqlite, readme_data_end

DB = "pos.sqlite"


def convert(shop_dir: Path) -> Converted:
    notes = []
    db = shop_dir / DB
    raw = read_sqlite(db, "chi_tiet_don")
    raw = drop_duplicate_lines(raw, "MaHoaDon", notes)

    staff = read_sqlite(db, "nhan_vien")
    staff_name = dict(zip(staff["MaNhanVien"], staff["TenGoi"].map(clean_text)))
    orders = read_sqlite(db, "don_hang")
    orders = pd.DataFrame({
        "order_id": orders["MaHoaDon"].map(clean_text),
        "order_date": dates(orders["NgayLap"]),
        "order_time": orders["ThoiGianLap"].map(parse_datetime),
        "status": orders["TinhTrang"].map(map_status),
        "customer_code": orders["MaKhachHang"].map(clean_text),
        "channel": orders["KenhBanHang"].map(map_channel),
        "payment_method": orders["PhuongThucTT"].map(map_payment),
        "seller": orders["MaNhanVien"].map(staff_name),
    })
    customers = read_sqlite(db, "khach_hang")
    customers = pd.DataFrame({
        "customer_code": customers["MaKhachHang"].map(clean_text),
        "customer_name": customers["TenGoi"].map(clean_text),
        "customer_group": customers["LoaiKhachHang"].map(norm_text),
    })
    products = read_sqlite(db, "san_pham")
    products = pd.DataFrame({
        "product_code": products["MaHangHoa"].map(clean_text),
        "product_name": products["TenGoi"].map(clean_text),
        "product_group": products["DanhMuc"].map(norm_text),
        "base_unit": products["DonViCoSo"].map(norm_text),
    })

    df = pd.DataFrame({
        "order_id": raw["MaHoaDon"].map(clean_text),
        "line_no": pd.to_numeric(raw["SoThuTu"]),
        "product_code": raw["MaHangHoa"].map(clean_text),
        "unit": raw["DonViTinh"].map(norm_text),
        "qty": pd.to_numeric(raw["SL"]).astype(float),
        "qty_base": pd.to_numeric(raw["SLQuyDoi"]).astype(float),
        "unit_price": money(raw["GiaDonVi"]),
        "line_discount": money(raw["ChietKhau"]),
        "line_amount": money(raw["TriGia"]),
        "cost_price": money(raw["GiaVonLucBan"]),
        "source_file": raw["source_file"], "source_sheet": raw["source_sheet"], "source_row": raw["source_row"],
    })
    n = len(df)
    df = df.merge(orders, on="order_id", how="left", validate="many_to_one")
    df = df.merge(customers, on="customer_code", how="left", validate="many_to_one")
    df = df.merge(products, on="product_code", how="left", validate="many_to_one")
    assert len(df) == n
    for col, src in (("status", "don_hang"), ("product_name", "san_pham")):
        if df[col].isna().any():
            notes.append(f"{int(df[col].isna().sum())} lines without a match in {src}")

    df["customer_key"] = df["customer_code"]  # shop has customer codes (§3); blank = walk-in
    df["product_key"] = df["product_code"]
    df["line_kind"] = "product"
    df["is_return_line"] = df["qty"] < 0
    df["unit_factor"] = df["qty_base"] / df["qty"]

    return Converted(df.drop(columns="customer_code"), [f"{DB} [chi_tiet_don, don_hang, khach_hang, san_pham, nhan_vien]"],
                     readme_data_end(shop_dir / "README.md"), notes)
