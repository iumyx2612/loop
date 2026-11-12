"""Step 3's anchor mapping (agreed with the user, 2026-11-05; explore_agent.md "Step 3: agreed structure").

An anchor is a column holding the same values in two tables: an order code, a product code or name, a customer
code or name. Another table feeds the sales lines only through an anchor: each line looks its value up in that
table (one row per order / product / customer) and takes the row's other columns.

ANCHOR_MAP: anchor field -> the fields looked up through it. A table that attaches by an anchor is asked only that
anchor's fields (those still open), so e.g. a customers table (anchor customer_code) is never asked order_date,
status or seller. A table attaching by several anchors (a products table with unique codes and names) is asked
the fields of all of them.

Open (explore_agent.md): seller as an anchor too (biz_123's staff table attaches to the orders by staff code).
"""
from src.datamate.analyst_agent.explore_agent.v2.planner.column_mapping.nodes import ASKED
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.nodes import FILLABLE

ANCHOR_MAP: dict[str, tuple[str, ...]] = {
    # an orders table (one row per order); customer_group too, since some orders tables have it
    "order_id": ("order_date", "order_time", "status", "channel", "payment_method", "seller",
                 "customer_name", "customer_code", "customer_group"),
    # a customers table (one row per customer), by code or by name
    "customer_code": ("customer_name", "customer_group"),
    "customer_name": ("customer_group", "customer_code"),
    # a products table (one row per product), by code or by name (biz_116, 117, 122: no code on the line)
    "product_code": ("product_name", "product_group", "base_unit"),
    "product_name": ("product_code", "product_group", "base_unit"),
}
ANCHOR_FIELDS = tuple(ANCHOR_MAP)  # the anchors, in this order

assert set(ANCHOR_FIELDS) <= set(ASKED), "every anchor must be a column step 2 asks (so the line can hold it)"
assert all(f in FILLABLE for fields in ANCHOR_MAP.values() for f in fields), "only fillable fields are looked up"
assert set(FILLABLE) <= {f for fields in ANCHOR_MAP.values() for f in fields}, \
    "every fillable field must be reachable through some anchor"
assert all(a not in fields for a, fields in ANCHOR_MAP.items()), "an anchor never looks itself up"


def related_fields(field: str) -> set[str]:
    """The fields that look the same thing up as `field`: every anchor whose ANCHOR_MAP family ({anchor} + its
    fields) holds `field`, joined, without `field` itself. order_id is left out: it looks up nearly every order
    field, so through it everything would be related. customer_group -> {customer_code, customer_name};
    product_name -> {product_code, product_group, base_unit}; order_date -> {} (agreed with the user, 2026-11-11:
    the snapshot retry lists these as known, since their meanings are close enough to be mixed up)."""
    related = set()
    for a, looked_up in ANCHOR_MAP.items():
        if a != "order_id" and field in {a, *looked_up}:
            related |= {a, *looked_up}
    related.discard(field)
    return related


def fields_for(anchors: list[str], to_fill: list[str]) -> list[str]:
    """The fields a table attaching by `anchors` is asked: their ANCHOR_MAP fields still in `to_fill`, in
    to_fill's order."""
    reachable = {f for a in anchors for f in ANCHOR_MAP.get(a, ())}
    return [f for f in to_fill if f in reachable]
