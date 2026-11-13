"""Step 7's prompt (nodes.map_line_kinds): one call per shop, one enum per candidate product. Target model ≤ 9B.

The rules come from the rulebook, quoted (the final test's rulebook may differ): the table rows saying what an
order's revenue is and that a Phí phục vụ line is not an item (RULE_ROWS, found by their label, whatever section they
are in). The three kinds are said in the rulebook's terms (an item = mặt hàng, revenue = doanh thu), so the model
decides from the quoted rule, not from knowing what a fee is.
"""

# the rulebook table rows quoted, by their first cell (§1 "Doanh thu một đơn", §2 "Mặt hàng" in train / public test)
RULE_ROWS = ("Doanh thu một đơn", "Mặt hàng")

# the reply's enum; the keys are what nodes.py does with each answer
KINDS = {
    "item": "a product or dish the shop sells (mặt hàng): counted, ranked, and part of revenue.",
    "revenue, not an item": "charged on the bill and part of the order's revenue (doanh thu), but not an item the "
                            "shop sells: never counted or ranked as an item.",
    "not revenue": "not part of the order's revenue: e.g. a discount or voucher on the whole order, points used, a "
                   "delivery fee.",
}

# Pass 2 (agreed 2026-11-11): each name pass 1 did not call an item is asked again alone, with a one-sentence reason
# before its kind (the reply's reason_1 field, then name_1). Why: tested on the 9B (scripts/explore/try_line_kinds.py), Voucher came back
# "revenue, not an item" alone without a reason and with all 14 names with or without reasons (with reasons Phí ship
# flipped to revenue too); alone with a reason, Voucher / Phí ship / Dùng # điểm were "not revenue" and Phí phục vụ
# "revenue, not an item", every call. Reasoning stays in the prompt's control (the model's own thinking is off).
# Word for word as tested in scripts/explore/try_line_kinds.py (2026-11-11): the first build reworded both ("First write one short sentence
# why (reason_1), then its kind (name_1)." / "… why this name is of that kind") and Phí ship came back revenue
WHY_LINE = "\nFor each name, first write one short sentence why (reason_1, ...), then its kind (name_1, ...)."
REASON = "one short sentence: why, for {pattern!r}"  # the reason field's description; {pattern}: the candidate

# {rulebook}, {kinds}, {key_note}, {names}, {examples}: nodes.line_kinds_message
LINE_KINDS = """A shop records its sales lines in a table: each line names something on a customer's bill. Most \
lines sell an item, but some may be something else written as a line. Decide what each name below is.

# The rules (from the rulebook)
{rulebook}

# The three kinds
{kinds}

# The names
The most frequent names of the shop{key_note}, and the names whose lines mostly have no quantity, no price or a \
negative amount. With how many lines and what share of the orders hold each:
{names}

Two lines of each:
{examples}

# Question
For each name (name_1, ...), which of the three kinds is it? Most names are items. Answer every name."""
