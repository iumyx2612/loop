"""Step 3's snapshot: every attached table is asked, on its own, which of its columns holds each field its anchors
look up (anchors.ANCHOR_MAP); the answers are checked with step 2's column checks. Conflicts go to the field resolve."""
from src.datamate.analyst_agent.explore_agent.v2.planner.fill_missing_schema.snapshot.nodes import snapshot

__all__ = ["snapshot"]
