"""Normalising chat-model output before it reaches a parser."""
import json

from backend.shared.utils.message_content import content_to_text, strip_code_fence


class TestContentToText:
    def test_plain_string_passes_through(self):
        assert content_to_text("hello") == "hello"

    def test_gemini_3_content_blocks_are_flattened(self):
        blocks = [{"type": "text", "text": "part one "}, {"type": "text", "text": "part two"}]
        assert content_to_text(blocks) == "part one part two"

    def test_non_text_blocks_are_ignored(self):
        blocks = [{"type": "thinking", "signature": "abc"}, {"type": "text", "text": "answer"}]
        assert content_to_text(blocks) == "answer"

    def test_none_becomes_empty(self):
        assert content_to_text(None) == ""


class TestStripCodeFence:
    """Models fence JSON despite format instructions, and not predictably.

    The same prompt returned bare JSON for a 5.7k contract and fenced JSON for a
    32.9k one, so extraction succeeded on one document and failed on the other
    with "Invalid json output".
    """

    def test_json_fence_is_removed(self):
        payload = {"clauses": []}
        fenced = f"```json\n{json.dumps(payload)}\n```"

        assert json.loads(strip_code_fence(fenced)) == payload

    def test_bare_fence_is_removed(self):
        fenced = '```\n{"a": 1}\n```'
        assert json.loads(strip_code_fence(fenced)) == {"a": 1}

    def test_surrounding_whitespace_is_tolerated(self):
        fenced = '\n\n```json\n{"a": 1}\n```\n\n'
        assert json.loads(strip_code_fence(fenced)) == {"a": 1}

    def test_unfenced_json_is_untouched(self):
        raw = '{"a": 1}'
        assert strip_code_fence(raw) == raw

    def test_fences_inside_the_payload_survive(self):
        """Only a fence wrapping the whole response is a wrapper."""
        raw = '{"note": "use ```code``` sparingly"}'
        assert strip_code_fence(raw) == raw

    def test_multiline_json_body_is_preserved(self):
        body = '{\n  "clauses": [\n    {"clause_type": "Liability"}\n  ]\n}'
        assert json.loads(strip_code_fence(f"```json\n{body}\n```")) == json.loads(body)
