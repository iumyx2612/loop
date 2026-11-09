"""Step 3's anchor resolve (find_anchors' part c): for an anchor several attached tables link by, one LLM call picks
the shop's own list (orders / products / customers); the others' claims are dropped and attach runs again."""
from src.datamate.analyst_agent.explore_agent.v2.fill_missing_schema.anchor_resolve.nodes import resolve_anchors

__all__ = ["resolve_anchors"]
