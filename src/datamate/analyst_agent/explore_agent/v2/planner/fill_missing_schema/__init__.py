"""Workflow step 3: fill the sales_lines columns step 2 did not fill from the shop's other tables. The filter
(nodes.fields_to_fill) keeps the open and failed columns another table can hold; then find_anchors/, attach.py
(the gate), anchor_resolve/ and snapshot/ (one call per attached table, nodes.table_snapshot)."""
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.nodes import (FILLABLE, LINE_ONLY,
                                                                                    failed_columns, fields_to_fill)

__all__ = ["fields_to_fill", "failed_columns", "FILLABLE", "LINE_ONLY"]
