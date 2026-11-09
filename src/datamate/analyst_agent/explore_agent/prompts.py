"""Prompts shared by every version of the explore_agent. Kept short and single-purpose: the target model is ≤ 9B.
Each version's own prompts live with its nodes (v1: v1/think/prompts.py, v1/tool/prompts.py)."""

# confirm_headers (nodes.py)
HEADER = """Below are the first rows of one sheet from a shop's Excel file. Some sheets start with title rows, \
a date range, a filter line or blank rows before the real column header.

Which row is the column header, i.e. the row whose cells name the columns of the data rows below it?
Our guess is row {guess}. Answer with the row number; keep {guess} if it is right.

{rows}"""

# The sales_lines columns the plan maps (plan_check.MAPPABLE + OPTIONAL), with what the rulebook needs from each
SCHEMA = """One row of sales_lines = one product line (or service-fee line) of an order. Order-level values (date, \
status, customer, channel, payment, seller) are copied onto every line of the order, from wherever the shop writes \
them (often a separate orders file). Product attributes (name, group, base unit) are copied from the product list.

Map each of these, or list it in missing_columns when the shop does not record it anywhere:
- order_id: order / invoice / bill code
- order_date: the day the order was placed (periods go by this date)
- order_time: time the order was placed or created
- status: order status (completed / cancelled / returned / in_delivery after mapping); in some books it is in a \
note column ("Ghi chú")
- customer_name: customer name or code as written on the order
- customer_group: customer group (lẻ / thân thiết / sỉ ...), usually from the customer list
- channel: sales channel (store, Zalo OA, phone, e-commerce)
- payment_method: cash / transfer / e-wallet / COD / debt
- seller: staff who sold (code or name)
- product_code, product_name: as written; the name may come from the product list when the line has only a code
- product_group: product group / category, usually from the product list
- unit: unit written on the line
- qty: quantity in that unit
- base_unit: the product's base (smallest) unit, usually from the product list or stock list
- unit_price: price per one unit, before discount
- line_amount: amount of the line after the line discount (this is revenue)
- cost_price: cost per unit written on the sale line (needed for gross profit)
Map these too when the shop records them (otherwise they are computed, do not list them as missing):
- qty_base: quantity already converted to the base unit; line_discount: discount on the whole line
- customer_code: the customer's code on the order (e.g. "Mã KH"), when the shop has customer codes: it identifies \
the customer
- line_product_name: the product name written on the sale line, when product_name comes from the product list \
(kept for lines whose code is not in the list)
Never map these, the converter computes them: customer_key, product_key, line_kind, is_return_line, return_date, unit_factor, \
source_file, source_sheet, source_row."""
