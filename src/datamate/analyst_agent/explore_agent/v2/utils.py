"""Shared by the workflow's steps: one structured-output call with its trace record, and the state to and from
JSON (so a run can stop after one step and a later run can go on from the saved state:
scripts/explore/run_sales_lines_v2.py saves it, run_column_mapping_v2.py loads it)."""
from langchain_core.messages import BaseMessage
from openai import LengthFinishReasonError
from langgraph.runtime import Runtime
from pydantic import BaseModel

from src.datamate.analyst_agent.explore_agent.state import ColumnSource, ExploreContext, StatusWord, TableRef
from src.datamate.analyst_agent.explore_agent.utils import raw_text, usage

# state field -> the model of its items
PYDANTIC_LISTS = {"line_tables": TableRef, "columns": ColumnSource, "statuses": StatusWord}


def call_structured(runtime: Runtime[ExploreContext], schema: type[BaseModel],
                    messages: list[BaseMessage]) -> tuple[BaseModel | None, object, dict]:
    """One structured-output call -> (parsed reply or None, raw AIMessage, record with usage, stop reason, and on
    a parse failure the error + raw reply). include_raw: a reply that does not fit the schema comes back as a
    parsing error instead of raising."""
    llm = runtime.context.model.with_structured_output(schema, method="json_schema", include_raw=True)
    try:
        result = llm.invoke(messages)
    except LengthFinishReasonError as e:
        # the OpenAI client raises (instead of a parsing error) when the reply hit max_tokens; the cut-off reply
        # is in e.completion: keep it, so a runaway reply can be read in the trace
        choice, u = e.completion.choices[0], e.completion.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", None) or 0
        return None, None, {"usage": {"input": u.prompt_tokens if u else 0,  # incl. cached, like utils.usage
                                      "output": u.completion_tokens if u else 0,
                                      "cache_read": cached, "cache_write": 0},
                            "stop_reason": choice.finish_reason,
                            "error": f"the reply was cut at max_tokens ({u.completion_tokens if u else '?'} tokens)",
                            "raw_reply": choice.message.content or ""}
    raw = result["raw"]
    meta = getattr(raw, "response_metadata", None) or {}
    # Anthropic reports stop_reason, OpenAI-compatible servers finish_reason
    record = {"usage": usage(raw), "stop_reason": meta.get("stop_reason") or meta.get("finish_reason")}
    if result["parsed"] is None:
        record["error"] = str(result.get("parsing_error"))[:500]
        record["raw_reply"] = raw_text(raw)
    return result["parsed"], raw, record


# --- the state to and from JSON. Most fields are plain JSON already (str, dict, list of dicts); the fields in
# PYDANTIC_LISTS hold Pydantic objects: model_dump() turns them into dicts, loading builds the objects again ---

def dump_state(state: dict) -> dict:
    out = {}
    for k, v in state.items():
        out[k] = [x.model_dump() for x in v] if k in PYDANTIC_LISTS else v
    return out


def load_state(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        out[k] = [PYDANTIC_LISTS[k](**x) for x in v] if k in PYDANTIC_LISTS else v
    return out
