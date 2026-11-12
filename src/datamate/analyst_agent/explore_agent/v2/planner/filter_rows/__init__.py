"""Workflow step 0: mark the rows that are not data (repeated headers, subtotal / footer / note rows) on every table
group (nodes.filter_rows; the code part in rows.py)."""
from src.datamate.analyst_agent.explore_agent.v2.planner.filter_rows.nodes import filter_rows

__all__ = ["filter_rows"]
