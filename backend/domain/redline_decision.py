"""Reviewer decisions on redlines.

The design doc makes human approval a POC deliverable: high-risk items route to a
lawyer who can accept, modify or reject the suggestion. Until now a decision
could be POSTed as a free-form string to a detached `(:LegalDecision)` node that
gated nothing — nothing downstream read it, and the redline itself carried no
state.

The failure cases were designed before the happy path, because this is the first
increment where getting them wrong loses human work rather than machine output:

  deciding on a redline that does not exist   -> 404, never a silent no-op
  deciding on another tenant's redline        -> 404, so existence does not leak
  MODIFIED without replacement text           -> rejected; there is nothing to apply
  APPROVED or REJECTED with replacement text  -> rejected; the intent is ambiguous
  deciding twice                              -> allowed, last wins, prior state returned
  re-analysis after a decision                -> decided redlines are preserved
  a viewer attempting to approve              -> 403, via a dedicated permission
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RedlineStatus(str, Enum):
    """Where a redline sits in review."""

    PENDING = "PENDING"     # drafted, nobody has looked at it
    APPROVED = "APPROVED"   # use the suggested text as drafted
    MODIFIED = "MODIFIED"   # use the reviewer's own text instead
    REJECTED = "REJECTED"   # keep the contract's original wording

    @property
    def is_decided(self) -> bool:
        """Whether a human has ruled on this.

        Decided redlines survive re-analysis: a model re-run must not discard a
        judgement a lawyer has already made.
        """
        return self is not RedlineStatus.PENDING


@dataclass(frozen=True)
class RedlineDecision:
    """A validated reviewer decision, ready to persist."""

    status: RedlineStatus
    final_text: str
    note: str = ""
    decided_by: str = "unknown"


class InvalidDecision(ValueError):
    """The decision cannot be applied as stated."""


def build_decision(
    status: RedlineStatus,
    *,
    original_text: str,
    suggested_text: str,
    edited_text: Optional[str] = None,
    note: str = "",
    decided_by: str = "unknown",
) -> RedlineDecision:
    """Resolve a decision into the text that would actually go into the contract.

    Making `final_text` explicit here means consumers never have to re-derive
    "what did the reviewer actually agree to" from a status plus three text
    fields — a reconstruction that is easy to get subtly wrong.
    """
    edited = (edited_text or "").strip()

    if status is RedlineStatus.MODIFIED:
        if not edited:
            raise InvalidDecision(
                "A MODIFIED decision needs edited_text — the reviewer's replacement "
                "wording is the whole point of the decision."
            )
        final_text = edited
    else:
        if edited:
            raise InvalidDecision(
                f"edited_text was supplied with a {status.value} decision. Use "
                f"MODIFIED to substitute your own wording; APPROVED takes the "
                f"suggestion as drafted and REJECTED keeps the original."
            )
        final_text = suggested_text if status is RedlineStatus.APPROVED else original_text

    if status is RedlineStatus.PENDING:
        raise InvalidDecision("PENDING is the starting state, not a decision")

    return RedlineDecision(
        status=status,
        final_text=final_text,
        note=note.strip(),
        decided_by=decided_by,
    )
