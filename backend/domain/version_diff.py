"""What moved between two rounds of a contract.

The question a legal team actually asks, and the one nothing else answers well:
*what changed since last round, and did the counterparty accept our redline?*

It is answerable at all only because Increment 7 gave chunks identities. The
diff is a sequence alignment over two lists of chunk hashes — `difflib` does the
alignment, and everything interesting is in how the result is interpreted:

* a `replace` block is a **modification** when the old and new text are close
  enough to be the same clause rewritten, and a **removal plus an addition**
  when they are not. One number decides which, and getting it wrong turns
  "they softened the indemnity" into "they deleted the indemnity and added an
  unrelated clause";
* a chunk that leaves one place and appears in another has **moved**, not
  changed. A relocated indemnity clause is a real negotiation signal, and
  reporting it as a deletion and an insertion hides that.

No I/O. Similarity is supplied by the caller, because the vectors live in the
graph and this module must stay testable with the database down.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence

#: Cosine similarity at or above which two chunks are the same clause reworded
#: rather than one clause replaced by another. Deliberately the same threshold
#: the advisory match uses: both are answering "is this the same text, changed?"
MODIFIED_THRESHOLD = 0.80


class ChangeKind(str, Enum):
    """What happened to one chunk between two versions."""

    UNCHANGED = "UNCHANGED"
    MODIFIED = "MODIFIED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    #: Same text, different place. Not a change to the words, but not nothing.
    MOVED = "MOVED"


@dataclass
class Change:
    """One entry in the change report."""

    kind: ChangeKind
    #: Position in the previous version, where there was one.
    from_order: Optional[int] = None
    #: Position in the new version, where there is one.
    to_order: Optional[int] = None
    from_hash: Optional[str] = None
    to_hash: Optional[str] = None
    heading: str = ""
    #: Only for MODIFIED: how alike the two texts are, 0.0–1.0.
    similarity: Optional[float] = None

    @property
    def is_substantive(self) -> bool:
        """Whether a reviewer needs to read this one.

        A move is reported but does not need re-reading; the words are identical.
        """
        return self.kind in (ChangeKind.MODIFIED, ChangeKind.ADDED, ChangeKind.REMOVED)


@dataclass
class ChangeReport:
    changes: List[Change] = field(default_factory=list)
    #: Chunks whose analysis can be reused wholesale, by hash.
    unchanged_hashes: set = field(default_factory=set)
    #: Hashes that are new to this version and must be analysed.
    changed_hashes: set = field(default_factory=set)
    #: Set when the two versions were chunked under different rules, which makes
    #: their hashes incomparable and every "change" below meaningless.
    comparable: bool = True
    incomparable_reason: str = ""

    def of_kind(self, kind: ChangeKind) -> List[Change]:
        return [c for c in self.changes if c.kind is kind]

    @property
    def summary(self) -> Dict[str, int]:
        counts = {kind.value.lower(): 0 for kind in ChangeKind}
        for change in self.changes:
            counts[change.kind.value.lower()] += 1
        return counts

    @property
    def has_substantive_changes(self) -> bool:
        return any(c.is_substantive for c in self.changes)


def profiles_comparable(previous, current) -> tuple:
    """Whether two versions' hashes mean the same thing.

    Chunk identity is only meaningful *within one set of rules*. Two versions
    chunked by different strategies, sizes or normaliser versions produce
    unrelated hashes, and a diff over them would report the entire contract as
    replaced — a confident, detailed and completely false answer, which is worse
    than refusing.

    The extractor is deliberately **not** part of this test. A different
    extractor does change the text and therefore the hashes, but the result is
    an honestly noisy diff rather than a meaningless one, and refusing to diff a
    contract because the PDF library changed would help nobody.
    """
    if previous is None or current is None:
        # Nothing recorded — anything before Increment 7 — so there is nothing
        # to contradict. Compare, and let the result speak for itself.
        return True, ""

    for field_name, label in (
        ("strategy", "chunking strategy"),
        ("min_chunk_size", "minimum chunk size"),
        ("max_chunk_size", "maximum chunk size"),
        ("normaliser_version", "text-normalisation version"),
        ("chunker_version", "chunker version"),
    ):
        was = getattr(previous, field_name, None)
        now = getattr(current, field_name, None)
        if was != now:
            return False, (
                f"the two rounds were chunked with a different {label} "
                f"({was} then, {now} now), so their chunks cannot be compared"
            )
    return True, ""


def diff_versions(
    previous: Sequence,
    current: Sequence,
    similarity: Optional[Callable[[str, str], Optional[float]]] = None,
    previous_profile=None,
    current_profile=None,
) -> ChangeReport:
    """Align two versions' chunks and say what happened to each.

    `previous` and `current` are sequences of objects with `.hash`, `.order` and
    optionally `.heading` — the membership lists of the two versions.

    `similarity(old_hash, new_hash)` returns cosine similarity over the stored
    embeddings, or None when either has no usable vector. Absent, every
    `replace` is reported as a removal plus an addition, which is the safe
    reading: it shows the reviewer both texts rather than claiming a link
    between them that nothing checked.
    """
    comparable, reason = profiles_comparable(previous_profile, current_profile)
    if not comparable:
        return ChangeReport(
            changes=[], comparable=False, incomparable_reason=reason,
            changed_hashes={c.hash for c in current},
        )

    previous = list(previous)
    current = list(current)
    old_hashes = [c.hash for c in previous]
    new_hashes = [c.hash for c in current]

    # `autojunk=False` is load-bearing. The default heuristic treats any element
    # appearing in more than 1% of a sequence longer than 200 as junk and
    # refuses to anchor on it — and on a long contract the repeated boilerplate
    # is exactly what the alignment needs to anchor on. Left on, the diff
    # silently misaligns and reports most of the document as replaced.
    matcher = difflib.SequenceMatcher(None, old_hashes, new_hashes, autojunk=False)

    changes: List[Change] = []
    unchanged: set = set()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                old, new = previous[i1 + offset], current[j1 + offset]
                unchanged.add(old.hash)
                changes.append(Change(
                    kind=ChangeKind.UNCHANGED,
                    from_order=old.order, to_order=new.order,
                    from_hash=old.hash, to_hash=new.hash,
                    heading=getattr(new, "heading", "") or getattr(old, "heading", ""),
                ))
        elif tag == "delete":
            changes.extend(_removed(previous[i1:i2]))
        elif tag == "insert":
            changes.extend(_added(current[j1:j2]))
        else:  # replace
            changes.extend(_replaced(previous[i1:i2], current[j1:j2], similarity))

    changes = _fold_moves(changes)
    changed = {c.hash for c in current} - unchanged
    return ChangeReport(changes=changes, unchanged_hashes=unchanged,
                        changed_hashes=changed)


def _removed(chunks: Sequence) -> List[Change]:
    return [Change(kind=ChangeKind.REMOVED, from_order=c.order, from_hash=c.hash,
                   heading=getattr(c, "heading", "") or "")
            for c in chunks]


def _added(chunks: Sequence) -> List[Change]:
    return [Change(kind=ChangeKind.ADDED, to_order=c.order, to_hash=c.hash,
                   heading=getattr(c, "heading", "") or "")
            for c in chunks]


def _replaced(old_chunks: Sequence, new_chunks: Sequence,
              similarity: Optional[Callable]) -> List[Change]:
    """Decide whether a replaced run is a rewrite or a swap.

    Paired positionally within the block, because that is the order they appear
    in the document and a reviewer reads them that way. Any surplus on either
    side is a plain removal or addition — there is nothing to pair it with.
    """
    changes: List[Change] = []
    paired = min(len(old_chunks), len(new_chunks))

    for index in range(paired):
        old, new = old_chunks[index], new_chunks[index]
        score = similarity(old.hash, new.hash) if similarity else None

        if score is not None and score >= MODIFIED_THRESHOLD:
            changes.append(Change(
                kind=ChangeKind.MODIFIED,
                from_order=old.order, to_order=new.order,
                from_hash=old.hash, to_hash=new.hash,
                heading=getattr(new, "heading", "") or getattr(old, "heading", ""),
                similarity=round(score, 3),
            ))
        else:
            # Not close enough to call the same clause — or nothing measured it.
            # Two entries, so the reviewer sees both texts rather than a claimed
            # link nothing checked.
            changes.extend(_removed([old]))
            changes.extend(_added([new]))

    changes.extend(_removed(old_chunks[paired:]))
    changes.extend(_added(new_chunks[paired:]))
    return changes


def _fold_moves(changes: List[Change]) -> List[Change]:
    """A hash that is both removed and added has moved, not changed.

    Identical text in a new position. Reporting it as a deletion and an
    insertion buries a real negotiation signal — a relocated indemnity clause is
    something a reviewer wants to know about — under two entries that each look
    like something it is not.
    """
    removed = {c.from_hash: c for c in changes if c.kind is ChangeKind.REMOVED}
    added = {c.to_hash: c for c in changes if c.kind is ChangeKind.ADDED}
    moved_hashes = set(removed) & set(added)
    if not moved_hashes:
        return changes

    folded: List[Change] = []
    emitted: set = set()
    for change in changes:
        digest = change.from_hash if change.kind is ChangeKind.REMOVED else change.to_hash
        if change.kind in (ChangeKind.REMOVED, ChangeKind.ADDED) and digest in moved_hashes:
            if digest in emitted:
                continue
            emitted.add(digest)
            folded.append(Change(
                kind=ChangeKind.MOVED,
                from_order=removed[digest].from_order,
                to_order=added[digest].to_order,
                from_hash=digest, to_hash=digest,
                heading=added[digest].heading or removed[digest].heading,
            ))
            continue
        folded.append(change)
    return folded


def cosine(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    """Cosine similarity, or None when either vector is missing or degenerate."""
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if not left_norm or not right_norm:
        return None
    return dot / (left_norm * right_norm)
