"""The workflow's state: the shared ShopState (filled by confirm_headers and prepare) plus one field per workflow
step. Each step fills its own part of the SalesLinePlan; the plan itself is put together at the end.

Step 1 (choose_line_tables) fills `line_tables`; step 2 (map_line_columns) fills `columns`, `open_columns` and
`column_problems`; step 3's find_anchors fills `anchor_tables`, attach `attached`, resolve_anchors `dropped_claims`
(and `attached` again), snapshot `fields_to_fill` and `snapshots` (as the first trial, fill_from_tables, did);
step 4 (report_missing) fills `missing_columns`.
"""
from src.datamate.analyst_agent.explore_agent.state import ColumnSource, ShopState, StatusWord, TableRef


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
    # Step 3 (fill_from_tables): the open / failed columns another table can hold (fill_missing_schema.fields_to_fill)
    fields_to_fill: list[str]
    # Step 3, find_anchors: one entry per table group, {"table": name, "line": bool, "anchors": {anchor field:
    # column}}; the line's from step 2, the others as the model claimed them (no value check yet)
    anchor_tables: list[dict]
    # Step 3, attach (code): the tables linked to the lines, the lines first (round 0); each {"table", "round",
    # "links": [{"anchor", "column", "to_table", "to_column", "coverage", "unique"}]} = the plan's joins
    # (+ "pair_with": a code + name pair attached together, attach.PAIRS)
    attached: list[dict]
    # Step 3, resolve_anchors: the claims dropped because another table was chosen for that anchor, {"table",
    # "anchor"}; attach ignores them (resolve_anchors re-attaches without them and rewrites `attached`)
    dropped_claims: list[dict]
    # Test knob, not set by the workflow: attach's uniqueness rule (default attach.MIN_UNIQUE = 0.99); the runner's
    # --min-unique 0.98 attaches the payments tables, to try the anchor resolve on them
    min_unique: float
    # Step 3, snapshot: one entry per attached table that was asked, {"table", "round", "anchors" (the ones it
    # linked by), "asked" (their fields still to fill), "filled": {field: column}, "failed": {field: problem}}.
    # Not merged: each table was asked on its own, so a field can have several candidates (the field resolve picks).
    # The first trial (fill_from_tables) writes the same key without round / anchors / asked (every non-line table,
    # every field)
    snapshots: list[dict]
    # Step 4, report_missing (code): {field: reason} for every plan_check.MAPPABLE field neither step 2 nor step 3
    # found; the reason starts with a code (line_only, failed_check, unreached, not_in_tables, failed_in_tables).
    # The plan's missing_columns = its keys
    missing_columns: dict[str, str]
    # Step 5 (map_statuses): one StatusWord per distinct non-blank word of the status column = the plan's statuses;
    # [] when there is no status column or no readable reply
    statuses: list[StatusWord]
    # Step 5: the status of an order whose status cell is still blank after forward fill (a leftover blank: on a row
    # whose order id is filled); None when the column has no leftover blanks (or no answer). The plan's blank_status
    blank_status: str | None
    # Test knob, not set by the workflow (default True): False = step 5 asks the LLM every word, ignoring the
    # known-word list (runner flag --no-known-words), to measure the LLM where the list knows every word
    known_status_words: bool
