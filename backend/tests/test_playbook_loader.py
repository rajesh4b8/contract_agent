"""Parsing and validating the playbook file.

Validation is strict on purpose. A silently dropped rule means contracts stop
being checked against it, with nothing in the output to say so — the same class
of quiet failure as the hardcoded clause stub.
"""
import pathlib
import textwrap

import pytest

from backend.infrastructure.playbook_loader import (
    DEFAULT_PLAYBOOK,
    VALID_SEVERITIES,
    parse_playbook,
)


def _write(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "playbook.yaml"
    path.write_text(textwrap.dedent(body))
    return path


MINIMAL = """
    name: Test Playbook
    version: "1.0"
    tenant_id: test-tenant
    rules:
      - id: PAY-001
        rule_text: Payment is due within thirty days.
        rule_type: mandatory
        applies_to: [general]
        severity: CRITICAL
        section_reference: Payment Terms
        redline_text: Payment is due within thirty (30) days.
"""


class TestTheShippedPlaybook:
    """The file the product actually loads must stay valid."""

    def test_it_parses(self):
        playbook = parse_playbook(DEFAULT_PLAYBOOK)

        assert playbook.rules, "the default playbook defines no rules"
        assert playbook.tenant_id

    def test_rule_ids_are_unique_and_stable_looking(self):
        ids = [r.id for r in parse_playbook(DEFAULT_PLAYBOOK).rules]

        assert len(ids) == len(set(ids))
        # Findings cite these ids, so they should be identifiers rather than prose.
        assert all(" " not in rule_id for rule_id in ids)

    def test_every_rule_has_a_redline_to_offer(self):
        for rule in parse_playbook(DEFAULT_PLAYBOOK).rules:
            assert rule.redline_text, f"{rule.id} has no redline_text"

    def test_severities_are_all_recognised(self):
        for rule in parse_playbook(DEFAULT_PLAYBOOK).rules:
            assert rule.severity in VALID_SEVERITIES

    def test_the_rules_the_old_hardcoded_checker_enforced_are_all_present(self):
        """Nothing was lost moving policy from Python into data."""
        sections = {r.section_reference for r in parse_playbook(DEFAULT_PLAYBOOK).rules}

        for topic in ("Payment Terms", "Limitation of Liability", "Indemnification",
                      "Termination", "Intellectual Property", "Confidentiality"):
            assert topic in sections, f"no rule covers {topic}"


class TestParsing:
    def test_whitespace_in_folded_text_is_normalised(self, tmp_path):
        path = _write(tmp_path, """
            name: T
            rules:
              - id: R-1
                rule_text: >-
                  A rule that wraps
                  across lines.
                rule_type: mandatory
                applies_to: [general]
                severity: LOW
                section_reference: S
        """)

        assert parse_playbook(path).rules[0].rule_text == "A rule that wraps across lines."

    def test_applies_to_defaults_to_general(self, tmp_path):
        path = _write(tmp_path, """
            name: T
            rules:
              - id: R-1
                rule_text: A rule.
                rule_type: mandatory
                severity: LOW
                section_reference: S
        """)

        assert parse_playbook(path).rules[0].applies_to == ["general"]

    def test_tenant_defaults_when_absent(self, tmp_path):
        assert parse_playbook(_write(tmp_path, MINIMAL)).tenant_id == "test-tenant"


class TestValidation:
    def test_a_missing_field_is_rejected(self, tmp_path):
        path = _write(tmp_path, """
            name: T
            rules:
              - id: R-1
                rule_text: A rule.
                rule_type: mandatory
                severity: LOW
        """)

        with pytest.raises(ValueError, match="section_reference"):
            parse_playbook(path)

    def test_an_unknown_severity_is_rejected(self, tmp_path):
        path = _write(tmp_path, """
            name: T
            rules:
              - id: R-1
                rule_text: A rule.
                rule_type: mandatory
                severity: VERY_BAD
                section_reference: S
        """)

        with pytest.raises(ValueError, match="severity"):
            parse_playbook(path)

    def test_duplicate_ids_are_rejected(self, tmp_path):
        """Two rules sharing an id makes a citation ambiguous."""
        path = _write(tmp_path, """
            name: T
            rules:
              - id: R-1
                rule_text: First.
                rule_type: mandatory
                severity: LOW
                section_reference: S
              - id: R-1
                rule_text: Second.
                rule_type: mandatory
                severity: HIGH
                section_reference: S
        """)

        with pytest.raises(ValueError, match="duplicate rule id"):
            parse_playbook(path)

    def test_a_playbook_with_no_rules_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="no rules"):
            parse_playbook(_write(tmp_path, "name: Empty\nrules: []\n"))

    def test_a_non_mapping_file_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="not a YAML mapping"):
            parse_playbook(_write(tmp_path, "- just\n- a list\n"))
