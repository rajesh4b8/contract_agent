"""A matter: one contract under negotiation, and every round of it.

Until this increment a review lived in one browser tab. The contract list was
in ``localStorage``, the selected contract was ``useState`` with no URL, and the
backend could not have answered even if the page had asked — there was no
list endpoint, and the analysis persisted counts rather than findings.

A matter is the durable object a reviewer leaves and comes back to:

    (:Matter)-[:HAS_VERSION {n}]->(:ContractVersion)-[:HAS_FINDING]->(:ClauseFinding)
                                                    -[:HAS_REDLINE]->(:Redline)

**One document per matter**, deliberately — no ``Matter -> Document -> Version``
nesting. An SOW issued under an MSA is a *link between two matters* by reference
number, which is an edge to add later, not a container to build now.

This module is the vocabulary: statuses and the transitions a human may make,
the reference-number scheme, and the source hash that decides whether an upload
is a new round or the same bytes twice. It holds no I/O, so every rule in it is
testable with the database down.
"""
from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


class MatterStatus(str, Enum):
    """Where a matter sits in the negotiation.

    ``DRAFT -> IN_REVIEW -> REVIEWED -> AWAITING_COUNTERPARTY -> CLOSED``

    Two of these are **derived, never stored**: ``IN_REVIEW`` and ``REVIEWED``
    are simply "some redlines are still pending" and "none are", which
    ``redline_review_summary()`` already computes. Storing them would give us a
    second copy of a fact the redlines already hold, free to drift from it.
    Only the transitions a human actually makes are written down.
    """

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    REVIEWED = "REVIEWED"
    AWAITING_COUNTERPARTY = "AWAITING_COUNTERPARTY"
    CLOSED = "CLOSED"

    @property
    def is_derived(self) -> bool:
        """True for the two statuses computed from the redline counts."""
        return self in (MatterStatus.IN_REVIEW, MatterStatus.REVIEWED)

    @property
    def accepts_new_versions(self) -> bool:
        """A closed matter takes no new rounds until someone reopens it."""
        return self is not MatterStatus.CLOSED


#: What a human may move a matter to, from each status. The two derived
#: statuses are never *targets* — a reviewer who wants a matter back in review
#: moves it to DRAFT, and the redline counts take over from there.
ALLOWED_TRANSITIONS: Dict[MatterStatus, frozenset] = {
    MatterStatus.DRAFT: frozenset({MatterStatus.AWAITING_COUNTERPARTY, MatterStatus.CLOSED}),
    MatterStatus.IN_REVIEW: frozenset({MatterStatus.AWAITING_COUNTERPARTY, MatterStatus.CLOSED}),
    MatterStatus.REVIEWED: frozenset({MatterStatus.AWAITING_COUNTERPARTY, MatterStatus.CLOSED}),
    MatterStatus.AWAITING_COUNTERPARTY: frozenset({MatterStatus.DRAFT, MatterStatus.CLOSED}),
    MatterStatus.CLOSED: frozenset({MatterStatus.DRAFT}),
}


class InvalidTransition(ValueError):
    """A status change that is not on the map."""


def parse_status(value: Any, default: MatterStatus = MatterStatus.DRAFT) -> MatterStatus:
    """Read a status out of the graph without letting a stray value crash a list.

    A matter written by an older build, or hand-edited, should render as DRAFT
    rather than 500 the whole matters page.
    """
    if isinstance(value, MatterStatus):
        return value
    try:
        return MatterStatus(str(value).strip().upper())
    except (ValueError, AttributeError):
        return default


def derive_status(stored: Any, review_summary: Optional[Dict[str, int]] = None) -> MatterStatus:
    """The status to show, given what is stored and how review is going.

    ``DRAFT`` is the only stored status that gives way to a derived one: once a
    redline exists, "draft" is no longer true, and whether the matter is
    ``IN_REVIEW`` or ``REVIEWED`` is exactly ``pending == 0``.
    ``AWAITING_COUNTERPARTY`` and ``CLOSED`` are human statements about the
    world and are never overridden by a count.
    """
    status = parse_status(stored)
    if status is not MatterStatus.DRAFT:
        return status

    summary = review_summary or {}
    total = summary.get("total") or 0
    if total <= 0:
        return MatterStatus.DRAFT
    return (MatterStatus.REVIEWED if (summary.get("pending") or 0) == 0
            else MatterStatus.IN_REVIEW)


def check_transition(current: MatterStatus, target: MatterStatus) -> MatterStatus:
    """Raise unless a human may move `current` to `target`."""
    if target.is_derived:
        raise InvalidTransition(
            f"{target.value} is derived from the redline decisions, not set directly. "
            f"Move the matter to DRAFT and it will follow the review."
        )
    if target is current:
        raise InvalidTransition(f"the matter is already {current.value}")
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        allowed = ", ".join(sorted(s.value for s in ALLOWED_TRANSITIONS.get(current, frozenset())))
        raise InvalidTransition(
            f"cannot move a {current.value} matter to {target.value}"
            + (f"; allowed from here: {allowed}" if allowed else "")
        )
    return target


class AnalysisStatus(str, Enum):
    """How far the analysis of one version got.

    ``FAILED`` exists so that a version whose analysis died is never rendered
    as a contract with no findings — the single worst thing this screen could
    do, because it reads as good news.
    """

    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


# --------------------------------------------------------------------------
# Reference numbers
# --------------------------------------------------------------------------

#: Substring -> code, most specific first. This is the thing people say out
#: loud and put in emails, so it is a short familiar acronym rather than a
#: UUID, and the list is deliberately small and legible.
TYPE_CODES: tuple = (
    ("master services agreement", "MSA"),
    ("master service agreement", "MSA"),
    ("msa", "MSA"),
    ("statement of work", "SOW"),
    ("sow", "SOW"),
    ("non-disclosure", "NDA"),
    ("nondisclosure", "NDA"),
    ("non disclosure", "NDA"),
    ("mnda", "NDA"),
    ("nda", "NDA"),
    ("data processing", "DPA"),
    ("dpa", "DPA"),
    ("software as a service", "SAAS"),
    ("saas", "SAAS"),
    ("subscription", "SUB"),
    ("licensing", "LIC"),
    ("license", "LIC"),
    ("licence", "LIC"),
    ("employment", "EMP"),
    ("lease", "LSE"),
    ("purchase order", "PO"),
    ("amendment", "AMD"),
    ("addendum", "ADD"),
    ("service agreement", "SVC"),
)

#: Used when the extracted type says nothing usable. "Contract" is honest.
FALLBACK_TYPE_CODE = "CTR"

REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,7}-\d{4}-\d{4,}$")


def type_code(contract_type: Optional[str]) -> str:
    """The ``MSA`` in ``MSA-2026-0042``.

    Unrecognised types get the first three letters of their first word rather
    than all collapsing into one ``CTR`` sequence — ``Consulting Agreement``
    becoming ``CON-2026-0001`` still reads as something, and the code is only
    ever computed once, at the moment the reference is allocated.
    """
    text = (contract_type or "").casefold()
    for needle, code in TYPE_CODES:
        if needle in text:
            return code

    words = re.findall(r"[A-Za-z]+", contract_type or "")
    if words and len(words[0]) >= 3:
        return words[0][:3].upper()
    return FALLBACK_TYPE_CODE


def format_reference(kind: str, year: int, n: int) -> str:
    """``MSA-2026-0042`` — sequential per tenant, per year, per type.

    Padded to four digits and allowed to grow past them: the padding is
    cosmetic, uniqueness is the counter's job, and truncating the ten-thousandth
    contract of a year into a collision would be a far worse outcome than an
    inconsistent width.
    """
    if n < 1:
        raise ValueError(f"reference numbers start at 1, got {n}")
    return f"{kind.upper()}-{int(year):04d}-{n:04d}"


def is_reference(value: Optional[str]) -> bool:
    """Cheap shape check, so obvious junk never reaches a query."""
    return bool(value) and bool(REFERENCE_PATTERN.match(value.strip().upper()))


# --------------------------------------------------------------------------
# Source identity
# --------------------------------------------------------------------------

def canonical_text(text: Optional[str]) -> str:
    """The text a hash is taken over: whitespace folded, nothing else.

    PDF extraction is not byte-stable across runs — line breaks and runs of
    spaces move — so hashing the raw string would report the same file as
    different on a re-upload. Folding whitespace is the smallest normalisation
    that survives that without claiming two genuinely different documents are
    the same.
    """
    return " ".join((text or "").split())


def source_sha256(text: Optional[str]) -> str:
    """Identity of a version's source text.

    The one automatic decision in this increment: an exact match is not a
    judgement call, so it never asks. Everything else — "is this a new round of
    that contract, or a different contract off the same template?" — is the
    user's to make.
    """
    return hashlib.sha256(canonical_text(text).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Pre-filling the confirmation card
# --------------------------------------------------------------------------

#: Roles that read as *our* side of the table. Everything else is a candidate
#: counterparty.
OUR_SIDE_ROLES = (
    "provider", "supplier", "vendor", "contractor", "licensor",
    "disclosing", "seller", "consultant", "company",
)


def counterparty_from_parties(parties: Optional[Iterable[Dict[str, Any]]]) -> str:
    """Best guess at who is across the table.

    There is no notion of "us" in the data yet, so this cannot be right every
    time — which is the whole reason the new-contract flow shows a card the
    user corrects rather than filing silently. Getting it approximately right
    means they edit a field instead of typing one.
    """
    named: List[Dict[str, Any]] = [
        p for p in (parties or [])
        if isinstance(p, dict) and (p.get("name") or "").strip()
    ]
    if not named:
        return ""

    for party in named:
        role = (party.get("role") or "").strip().casefold()
        if role and not any(marker in role for marker in OUR_SIDE_ROLES):
            return party["name"].strip()
    return named[0]["name"].strip()


def suggest_title(contract_type: Optional[str], counterparty: Optional[str],
                  filename: Optional[str] = None) -> str:
    """A human-readable name for the matter, pre-filled and editable."""
    kind = (contract_type or "").strip()
    other = (counterparty or "").strip()
    if kind and other:
        return f"{kind} — {other}"
    if other:
        return other
    if kind:
        return kind
    name = (filename or "").strip()
    return re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE) or "Untitled contract"
