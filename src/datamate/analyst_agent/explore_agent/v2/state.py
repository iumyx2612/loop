"""The workflow's state: the shared ShopState (filled by confirm_headers and prepare) plus one field per workflow
step. Each step fills its own part of the SalesLinePlan; the plan itself is put together at the end.

Step 1 (choose_line_tables) fills `line_tables`; step 2 (map_line_columns) fills `columns`, `open_columns` and
`column_problems`; step 3's find_anchors fills `anchor_tables`, attach `attached`, resolve_anchors `dropped_claims`
(and `attached` again), snapshot `fields_to_fill` and `snapshots`;
step 4 (report_missing) fills `missing_columns`; step 5 (map_statuses) `statuses` and `blank_status`; step 6
(cleaning_inputs) `forward_fill_columns`, `return_lines` and `notes`; step 7 (map_line_kinds) `service_fee_names`
and `non_revenue_names`; step 8 (write_plan) `plan` and `plan_problems`.
"""
from src.datamate.analyst_agent.explore_agent.state import (
    ColumnSource, ConversionSource, ShopState, StatusWord, TableRef,
)


class WorkflowState(ShopState):
    # Step 0 (filter_rows): rows that are not data, explore.table_key(file, sheet) -> row numbers as in the file
    # (repeated header rows + rows of the patterns the LLM judged summary / note). Marked, not deleted
    skipped_rows: dict[str, list[int]]
    # Step 0: per table group (step 1's option name) the regexes of its summary patterns, for the plan's
    # subtotal_labels; matched case-insensitively against the row's word cells joined by ' | ' (filter_rows.rows)
    subtotal_labels: dict[str, list[str]]
    # Step 1: the tables whose rows are sales lines, one TableRef per sheet / file of the chosen table group
    # (month sheets and split files listed one by one); [] when the step could not choose
    line_tables: list[TableRef]
    # Step 2: the sales_lines columns found in the line tables (file / sheet = the first line table; plan_check
    # reads a line-table column over all line tables)
    columns: list[ColumnSource]
    # Step 2: the columns the model answered "(not in this file)" for: open for step 3 (the other tables)
    open_columns: list[str]
    # Step 2: what the checks found. A column check failed (e.g. a text column for line_amount): that answer is
    # neither mapped nor open. The cross-column check failed (line_amount ≠ qty × unit_price − line_discount on
    # most lines): the fields stay mapped, since the check cannot tell which one is wrong. No retry yet
    column_problems: list[str]
    # Step 3 (snapshot): the open / failed columns another table can hold (fill_missing_schema.fields_to_fill)
    fields_to_fill: list[str]
    # Step 3, find_anchors: one entry per table group, {"table": name, "line": bool, "anchors": {anchor field:
    # column}}; the line's from step 2, the others as the model claimed them (no value check yet)
    anchor_tables: list[dict]
    # Step 3, attach (code): the tables linked to the lines, the lines first (round 0); each {"table", "round",
    # "links": [{"anchor", "column", "to_table", "to_column", "coverage", "unique"}]} = the plan's joins
    # (+ "pair_with": a code + name pair attached together, attach.PAIRS; + "alias": True for a name + its other
    # spelling, attach._alias_links)
    attached: list[dict]
    # Step 3, resolve_anchors: the claims dropped because another table was chosen for that anchor, {"table",
    # "anchor"}; attach ignores them (resolve_anchors re-attaches without them and rewrites `attached`)
    dropped_claims: list[dict]
    # Test knob, not set by the workflow: attach's uniqueness rule (default attach.MIN_UNIQUE = 0.99); the runner's
    # --min-unique 0.98 attaches the payments tables, to try the anchor resolve on them
    min_unique: float
    # Step 3, snapshot: one entry per attached table that was asked, {"table", "round", "anchors" (the ones it
    # linked by), "asked" (their fields still to fill), "filled": {field: column}, "failed": {field: problem}}.
    # Not merged: each table was asked on its own, so a field can have several candidates (the field resolve picks)
    snapshots: list[dict]
    # Step 4, report_missing (code): {field: reason} for every plan_check.MAPPABLE field neither step 2 nor step 3
    # found; the reason starts with a code (line_only, failed_check, unreached, not_in_tables, failed_in_tables).
    # The plan's missing_columns = its keys
    missing_columns: dict[str, str]
    # Step 5 (map_statuses): one StatusWord per distinct non-blank word of the status column = the plan's statuses;
    # [] when there is no status column or no readable reply
    statuses: list[StatusWord]
    # Step 5: the status of an order whose status cell is still blank after forward fill (a leftover blank: on a row
    # whose order id is filled), or of every order when the shop has no status column (5e); None when the column has
    # no leftover blanks (or no answer). The plan's blank_status
    blank_status: str | None
    # Test knob, not set by the workflow (default True): False = step 5 asks the LLM every word, ignoring the
    # known-word list (runner flag --no-known-words), to measure the LLM where the list knows every word
    known_status_words: bool
    # Step 6a (cleaning_inputs, code): the line-table columns the converter forward fills, order id first = the
    # plan's forward_fill_columns; [] when no order id is mapped or it is never blank
    forward_fill_columns: list[str]
    # Step 6b (cleaning_inputs, code): returns are written as negative-quantity lines (at least one data row with a
    # negative qty) = the plan's return_lines; False when qty is not mapped
    return_lines: bool
    # The plan's notes: anything else the converter must know. Step 6c writes the duplicate-line count (the only
    # writer so far: a later step adding notes must keep these, e.g. a reducer or reading them first)
    notes: list[str]
    # Step 7 (map_line_kinds): the product-name spellings of rows that are revenue but not an item (Phí phục vụ) =
    # the plan's service_fee_names, and of rows that are not sales (voucher, points, shipping) = non_revenue_names;
    # [] when none (or no readable reply)
    service_fee_names: list[str]
    non_revenue_names: list[str]
    # Conversions step (map_conversions): every place the shop writes its unit conversions = the plan's conversions;
    # [] when it has none. Absent from states saved before the step: write_plan then notes that it was not explored
    conversions: list[ConversionSource]
    # Step 8 (write_plan, code): the SalesLinePlan put together from the fields above, as a dict
    # (SalesLinePlan.model_dump(); SalesLinePlan(**plan) rebuilds it); None when step 1 chose no line tables
    plan: dict | None
    # Step 8: plan_check's problems with that plan ([] = it passed); recorded, no retry yet
    plan_problems: list[str]
