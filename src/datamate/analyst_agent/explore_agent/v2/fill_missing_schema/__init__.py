"""Workflow step 3: fill the sales_lines columns step 2 did not fill from the shop's other tables (in progress).
So far: the filter (nodes.fields_to_fill), which keeps the open and failed columns another table can hold, and the
snapshot node (nodes.fill_from_tables): one call per non-line table, no gate and no merge yet."""
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.nodes import (FILLABLE, LINE_ONLY,
                                                                                    failed_columns, fields_to_fill,
                                                                                    fill_from_tables)

__all__ = ["fields_to_fill", "failed_columns", "fill_from_tables", "FILLABLE", "LINE_ONLY"]
