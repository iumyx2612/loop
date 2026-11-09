"""think's structured output. The graph state think reads and writes is v1/state.py ExploreAgentState."""
from pydantic import BaseModel, Field


class ThinkOutput(BaseModel):
    notes: list[str] = Field(default_factory=list, description="New findings from this round's report, one short "
                             "note each, with exact file / sheet / column names and values. Do not repeat earlier notes.")
    request: str | None = Field(default=None, description="One short sentence asking the search agent for exactly "
                                "one thing still missing. Empty once your notes cover everything: the plan is then "
                                "written from your notes.")
