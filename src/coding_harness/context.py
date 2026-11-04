"""Context management: what the model sees, within a documented size limit.

The full conversation is kept in the session; the model gets a *view* of it:

- pinned, never shortened: the system prompt and the task message (request, scope, allowed files,
  check names). These are the trusted instructions.
- the most recent ``keep_recent`` messages, verbatim (long tool outputs may be shortened last).
- everything older is replaced by one summary message, one line per action.

All repository text and tool output reaches the model inside ``<untrusted>`` blocks, and the system
prompt says to treat it as data. Sizes are counted in characters (about 4 per token).
"""

from __future__ import annotations

import json
import re
from typing import Any

from coding_harness.config import ContextConfig
from coding_harness.navigation import Navigator
from coding_harness.output import shorten

Message = dict[str, Any]
PINNED = 2  # system prompt + task message
STOP_WORDS = {"that", "this", "with", "from", "must", "into", "have", "when", "then", "they", "their", "there",
              "which", "would", "should", "could", "does", "make", "sure", "only", "also", "change", "changes",
              "existing", "keep", "working", "add", "new", "and", "the", "for", "not", "its", "was", "are"}  # fmt: skip


def untrusted(source: str, text: str) -> str:
    """Mark text from the repository or from a command as data. It cannot close the block early."""
    source = source.replace('"', "'")
    text = re.sub(r"</?\s*untrusted", lambda m: m.group(0).replace("untrusted", "untrusted_"), text, flags=re.I)
    return f'<untrusted source="{source}">\n{text}\n</untrusted>'


def words(text: str) -> set[str]:
    """Lower-case words of a request or a name, with snake_case and CamelCase split up."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) >= 3 and w not in STOP_WORDS}


class ContextManager:
    def __init__(self, config: ContextConfig, navigator: Navigator) -> None:
        self.config = config
        self.navigator = navigator

    # --- the first context: a map of the repository and the files that look relevant ---

    def initial_context(self, request: str) -> str:
        """Repository map plus the start of the selected files, as untrusted blocks."""
        parts = ["Starting context selected by the harness (repository content, untrusted):",
                 untrusted("repository map", self.navigator.repo_map(self.config.map_chars))]  # fmt: skip
        for path in self.select_files(request):
            text, _ = shorten(self.navigator.tools.read_file(path), self.config.file_chars)
            parts.append(untrusted(f"file {path}", text))
        return "\n\n".join(parts)

    def select_files(self, request: str) -> list[str]:
        """The Python files whose path and definitions share the most words with the request."""
        wanted = words(request)
        scored = []
        for line in self.navigator.repo_map(10**9).splitlines():
            path, _, definitions = line.partition(": ")
            if not path.endswith(".py"):
                continue
            score = len(wanted & words(path)) * 2 + len(wanted & words(definitions))
            if "test" in path:
                score -= 1  # tests repeat every domain word; prefer the code they test
            if score > 0:
                scored.append((-score, len(path), path))
        return [path for *_, path in sorted(scored)[: self.config.select_files]]

    # --- the view sent with every model request ---

    def view(self, messages: list[Message]) -> list[Message]:
        """The messages to send: within ``max_chars`` unless the pinned messages alone are larger."""
        limit, keep = self.config.max_chars, self.config.keep_recent
        if size(messages) <= limit:
            return messages
        pinned, rest = messages[:PINNED], messages[PINNED:]
        older, recent = rest[:-keep] if keep else rest, [dict(m) for m in rest[-keep:]] if keep else []
        lines = [line for message in older for line in _summary_lines(message)]

        def build() -> list[Message]:
            if not lines:
                return [*pinned, *recent]
            text = "\n".join(lines)
            summary = ("Earlier steps, summarized by the harness to stay within the context limit. "
                       "Re-read a file before editing it.\n" + untrusted("earlier steps", text))  # fmt: skip
            return [*pinned, {"role": "user", "content": summary}, *recent]

        view = build()
        while size(view) > limit and lines:
            del lines[0]  # the oldest steps go first
            view = build()
        # Still too large: shorten the longest recent tool outputs, never the pinned instructions.
        for message in sorted((m for m in recent if m["role"] == "tool"), key=lambda m: -len(m["content"])):
            excess = size(view) - limit
            if excess <= 0:
                break
            message["content"] = shorten(message["content"], max(len(message["content"]) - excess, 200))[0]
            view = build()
        return view


def size(messages: list[Message]) -> int:
    return sum(len(str(m.get("content", ""))) + len(json.dumps(m.get("tool_calls", ""))) for m in messages)


def _summary_lines(message: Message) -> list[str]:
    content = " ".join(str(message.get("content", "")).split())
    match message["role"]:
        case "assistant":
            calls = [f"{c['function']['name']}({json.dumps(c['function']['arguments'], default=str)[:100]})"
                     for c in message.get("tool_calls", [])]  # fmt: skip
            said = [f"you: {content[:150]}"] if content else []
            return said + [f"you called {c}" for c in calls]
        case "tool":
            # Drop the untrusted wrapper of the original result: the whole summary is wrapped again.
            content = re.sub(r"</?untrusted[^>]*>", "", content).strip()
            return [f"  -> {message.get('tool_name', 'tool')}: {content[:150]}"]
        case _:
            return [f"harness: {content[:150]}"]
