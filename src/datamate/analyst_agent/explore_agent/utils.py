"""Small helpers shared by the explore_agent's nodes (every version): rulebook sections for the prompts, the
trace records' token usage and timing, and the cached system prompt."""
import re
import time

from langchain_core.messages import HumanMessage, SystemMessage

RULEBOOK_SECTIONS = (1, 3, 6, 8)  # orders and revenue, rankings (what a product is), industries, how files are written


def rulebook_sections(text: str, numbers=RULEBOOK_SECTIONS) -> str:
    """The rulebook's '## <n>. ...' sections with the given numbers (each with its ### subsections)."""
    parts = re.split(r"(?m)^(?=## \d+\.)", text)
    keep = [p.strip() for p in parts if (m := re.match(r"## (\d+)\.", p)) and int(m[1]) in numbers]
    return "\n\n".join(keep)


def usage(m) -> dict:
    """Token usage of one AIMessage: input, output, cache read, cache write (langchain-anthropic reports cache
    writes under ephemeral_5m / ephemeral_1h)."""
    meta = getattr(m, "usage_metadata", None) or {}
    details = meta.get("input_token_details") or {}
    write = sum(details.get(k) or 0 for k in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    return {"input": meta.get("input_tokens", 0), "output": meta.get("output_tokens", 0),
            "cache_read": details.get("cache_read") or 0, "cache_write": write}


def since(start: float) -> float:
    """Seconds since `start` (a time.perf_counter() value), rounded for the trace."""
    return round(time.perf_counter() - start, 2)


def label(file: str, sheet: str | None) -> str:
    return file if sheet is None else f"{file} [{sheet}]"


def raw_text(m) -> str:
    """The raw reply's text, for the trace when it could not be parsed."""
    if m is None:
        return ""
    if isinstance(m.content, str):
        return m.content[:4000]
    return " ".join(str(b.get("text") or b.get("input") or "") for b in m.content if isinstance(b, dict))[:4000]


def cached(system: str, user: str) -> list:
    """System + user messages, the system prompt marked for prompt caching: it is the same in every call of every
    shop of a split (as in the query_agent)."""
    return [SystemMessage(content=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]),
            HumanMessage(user)]
