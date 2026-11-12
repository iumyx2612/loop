"""resolve_anchors' prompt (nodes.choose_source): which of several tables is the list the anchor is looked up in."""

# What one row of the right table is, per anchor
SUBJECTS = {"order_id": "order (invoice / bill)", "customer_code": "customer", "customer_name": "customer",
            "product_code": "product", "product_name": "product"}

# {anchor}, {meaning}, {subject}, {options}: nodes.source_message
CHOOSE_SOURCE = """A shop's sales lines look up more information through the {anchor} ({meaning}). Several of \
the shop's tables have one row per {anchor} value, so each could be that lookup table.

{options}

# Question
Which of these tables is the shop's own list of {subject}s, one row per {subject}? A table that records something \
else and only mentions the {subject} (a payment, a stock count, a delivery, ...) is not.
Answer with the option's name exactly as written after "Option:"."""
