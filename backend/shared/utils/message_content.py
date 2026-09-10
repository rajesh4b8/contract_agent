"""Helpers for normalising LangChain chat-model output.

Newer Gemini (3.x) models via ``langchain_google_genai`` return ``message.content``
as a list of content blocks (``[{"type": "text", "text": ...}, ...]``) rather than
a plain string. Code that feeds ``response.content`` into string parsers must
flatten it first.
"""
import re
from typing import Any

# ```json ... ``` or bare ``` ... ``` wrapping the whole response.
_FENCED = re.compile(r"^\s*```(?:json|JSON)?\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)


def content_to_text(content: Any) -> str:
    """Flatten a LangChain message ``content`` value to plain text.

    Accepts a string (returned as-is), or a list of content blocks where each
    block is either a string or a dict with a ``text`` key. Non-text blocks
    (images, tool calls, thinking signatures) are ignored.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content) if content is not None else ""


def strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence, if the model added one.

    Models asked for JSON often answer with ```json ... ``` despite format
    instructions, and pydantic parsers reject that with "Invalid json output".
    Whether a fence appears varies by request, so the same prompt can succeed on
    one contract and fail on the next.

    Text that is not fenced is returned unchanged.
    """
    match = _FENCED.match(text)
    return match.group("body") if match else text
