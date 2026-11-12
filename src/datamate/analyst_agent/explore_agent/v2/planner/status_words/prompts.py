"""Step 5c's prompt (nodes.map_statuses): one call per shop, one enum per word the known-word list does not have.
Target model ≤ 9B.

The definitions come from the rulebook (§1 and §8, quoted: the final test's rulebook may differ), plus one line per
status saying what it means, never which words mean it (no word lists: user, 2026-11-09)."""

# §8 how files are written (the four statuses, blanks, status in a notes column). §1 (orders and revenue) dropped
# (user, 2026-11-09): mostly revenue rules, noise for this question
RULEBOOK_SECTIONS = (8,)

# what each status means; the keys are the plan's Status values (state.Status)
MEANINGS = {
    "completed": "the sale is done (hoàn thành).",
    "cancelled": "the sale will not happen: cancelled by the shop or the customer, or the goods could not be "
                 "delivered (huỷ).",
    "returned": "goods came back after the sale, all of them or only part of them (trả hàng).",
    "in_delivery": "the order is not finished yet: still being processed, delivered or waited for (đang giao).",
}

# The prompt is put together from blocks (nodes.status_message): a shop may need the words, the leftover blanks or
# both (all words known + leftover blanks: only the blank question).

# this shop's words already known (nodes.KNOWN), shown as examples of how the shop writes
KNOWN_BLOCK = """This shop's words already known:
{known}
"""

WORDS_BLOCK = """Its words to map, with how many rows hold each:
{words}

One row using each word:
{examples}
"""

# leftover blanks (words.Words.leftover); {orders}: BLANK_ORDERS or "" (no order id); {note}: BLANK_NOTE or "".
# The share of all rows (2026-11-11, agreed, option b): a fact code knows; "98% of the rows" makes a status other than
# the usual one look implausible without code deciding it (dev_013 on the 9B answered in_delivery for 98% of lines)
BLANK_BLOCK = """The column is also blank on {n:,} of its {rows:,} rows ({pct}%{orders}).{note} {k} of them:
{examples}
"""
BLANK_ORDERS = ", {m:,} orders"
# With an order id column (2026-11-11, agreed): a fact about the data that code knows (a leftover blank's row has its
# order id), never a rulebook rule (the final rulebook may differ). Replaced "rows that start an order (on the other
# lines of an order a blank repeats the order's first line; those are not counted)": false for a shop writing the
# order id on every line (dev_013), and it described the §8 row about later lines of a group ("không có nghĩa là …
# đơn hoàn thành"), so the 9B answered in_delivery. True for both kinds of shop, so no branching on continuation lines.
BLANK_NOTE = (" Each of these rows has its order id written, so the blank is not a later line of an order repeating "
              "the order's first line: the order itself has no word in this column.")

Q_WORDS = "For each word (word_1, ...), which of the four statuses does it mean? Answer every word."
# 2026-11-11, agreed (option a): describes the situation (the column names no status for the order), close to how a
# rulebook would say it, never which rulebook row applies. Was "... when this column is blank on an order's first
# line?", which pointed at the later-lines row of §8
Q_BLANK = "For `blank`: which status does an order have when this column names no status for it (the cell is blank)?"
# The blank question is its own call with a one-sentence reason first (agreed 2026-11-11, user: reason on the blank
# only; the words keep their call, bare enums). Word for word as tested (zalo_main.py --blank): dev_011 / dev_014
# (status ← Ghi chú, only return words) answered in_delivery without it, completed with it, citing §8's notes row.
BLANK_WHY = "\nFirst write one short sentence why (reason), then the status (blank)."
BLANK_REASON = "one short sentence: why, for blank"

# {rulebook}: utils.rulebook_sections; {meanings}, {body}, {question}: nodes
STATUS_WORDS = """A shop records its sales in tables. One column holds the status of each order, in the shop's own \
words. Map them to one of four statuses.

# The rules for order statuses (from the rulebook)
{rulebook}

# The four statuses
{meanings}

# The status column
Column '{column}'{meaning}.
{body}
# Question
{question}"""
