"""Bounding text that flows back to the model or the user."""

MARKER = "[... output shortened by the harness: {omitted} omitted ...]"


def shorten(text: str, limit: int) -> tuple[str, bool]:
    """Keep the head and the tail of ``text`` within ``limit`` characters and mark the cut.

    The tail is kept because test runners print their summary last.
    """
    if len(text) <= limit:
        return text, False
    head = limit // 2
    tail = limit - head
    marker = MARKER.format(omitted=f"{len(text) - limit} characters")
    return f"{text[:head]}\n{marker}\n{text[len(text) - tail:]}", True
