"""Approximation of the official grader (rulebook §9); verified: all 3000 train key answers score correct against themselves."""
import re
import unicodedata


def _norm_text(value) -> str:
    """Rulebook §9: strings compare ignoring case, whitespace and punctuation."""
    text = unicodedata.normalize("NFC", str(value)).casefold()
    return re.sub(r"[\s\W_]+", "", text)


def _same_value(got, expected, aliases=()) -> bool:
    if isinstance(expected, bool) or isinstance(got, bool):
        return got is expected
    if isinstance(expected, (int, float)):
        return isinstance(got, (int, float)) and abs(got - expected) < 1e-6
    if isinstance(expected, str):
        return _norm_text(got) in {_norm_text(v) for v in (expected, *aliases)}
    return got == expected


def is_correct(submission: dict, expected: dict) -> bool:
    """Approximate the official grader from rulebook §9 (type must match, then the value)."""
    kind = expected["type"]
    if submission.get("type") != kind:
        return False
    if kind == "unanswerable":
        return True

    got, answer, alias = submission.get("answer"), expected["answer"], expected.get("alias") or []
    if kind == "string":
        return _same_value(got, answer, alias)
    if kind == "list":
        if not isinstance(got, list) or len(got) != len(answer):
            return False
        return all(
            _same_value(g, a, alias[i] if i < len(alias) else ())
            for i, (g, a) in enumerate(zip(got, answer))
        )
    if kind == "table":
        # Row order does not matter; the first column is the name, which may also match its alias (a code)
        if not isinstance(got, dict) or not isinstance(got.get("rows"), list):
            return False
        expected_rows = [(row, alias[i] if i < len(alias) else ()) for i, row in enumerate(answer["rows"])]
        if len(got["rows"]) != len(expected_rows):
            return False
        unmatched = list(expected_rows)
        for row in got["rows"]:
            match = next(
                (e for e in unmatched
                 if len(row) == len(e[0])
                 and _same_value(row[0], e[0][0], e[1])
                 and all(_same_value(g, a) for g, a in zip(row[1:], e[0][1:]))),
                None,
            )
            if match is None:
                return False
            unmatched.remove(match)
        return True
    return _same_value(got, answer)
