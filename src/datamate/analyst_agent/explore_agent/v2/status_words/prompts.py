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

# leftover blanks (words.Words.leftover); {where}: "rows that start an order" or "rows" (no order id)
BLANK_BLOCK = """The column is also blank on {n:,} {where}{note}. {k} of them:
{examples}
"""
BLANK_NOTE = " (on the other lines of an order a blank repeats the order's first line; those are not counted)"

Q_WORDS = "For each word (word_1, ...), which of the four statuses does it mean? Answer every word."
Q_BLANK = "For `blank`: which status does an order have when this column is blank{on_first}?"

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
