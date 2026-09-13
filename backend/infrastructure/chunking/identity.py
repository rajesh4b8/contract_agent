"""Turning a document into chunks that know what they are.

The strategies in this package answer "where do the boundaries go". This module
answers "what is each piece, independent of the upload that produced it" — the
hash, the heading held outside it, and the position in the version.

It also pins the strategy. Selection is threshold-based scoring over section
counts and clause density (`strategy_selector.py`), and those thresholds can
flip on a one-word edit: a document chunked by `section` in v1 and by
`paragraph` in v2 has no chunk in common, for no reason a reader could ever see.
So selection runs once, for version 1, and later rounds are handed the recorded
profile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.domain.chunking import (
    ChunkingProfile,
    canonical,
    chunk_hash,
    split_heading,
)
from backend.infrastructure.chunking.section_strategy import SectionStrategy
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IdentifiedChunk:
    """One chunk, and everything about it that does not depend on the upload."""

    order: int
    hash: str
    content: str
    #: Held here and stored on the membership relationship, never in the hash.
    heading: str = ""
    chunk_type: str = "section"

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class ChunkedDocument:
    chunks: List[IdentifiedChunk]
    profile: ChunkingProfile
    #: True when no section pattern matched anywhere and the chunker fell back
    #: to packing text greedily. Said out loud rather than pretended away: a
    #: greedy packer has no boundary locality at all, so one insertion shifts
    #: every boundary after it and invalidates every hash downstream. The
    #: principled fix is content-defined chunking (a rolling fingerprint, the
    #: way rsync and restic place boundaries); until then, flagging it is the
    #: honest minimum.
    boundaries_are_structural: bool = True

    @property
    def hashes(self) -> List[str]:
        return [c.hash for c in self.chunks]

    @property
    def distinct_hashes(self) -> set:
        return set(self.hashes)


def _strategy_for(profile: ChunkingProfile):
    """The chunker the profile names.

    Only `section` is pinned today; anything else falls back to it and says so,
    because chunking a later round differently from its first round is the one
    outcome that makes every hash worthless.
    """
    if profile.strategy != "section":
        logger.warning(
            f"Chunking profile asks for {profile.strategy!r}, which is not pinned "
            f"for content addressing; using 'section'"
        )
    return SectionStrategy(
        min_chunk_size=profile.min_chunk_size,
        max_chunk_size=profile.max_chunk_size,
    )


def identify_chunks(text: str, profile: Optional[ChunkingProfile] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> ChunkedDocument:
    """Chunk a document and give every piece a content-derived identity.

    Deterministic: the same text and profile produce the same hashes, in the
    same order, on any machine. That is the whole basis for saying two versions
    share a chunk.
    """
    profile = profile or ChunkingProfile()
    if not (text or "").strip():
        return ChunkedDocument(chunks=[], profile=profile)

    strategy = _strategy_for(profile)
    raw = strategy.chunk_text(text, metadata or {})

    # Asked of the text, not inferred from the result. `_identify_sections`
    # returns a trailing block for any non-empty input, so a document where no
    # pattern matched comes back looking identical to a well-structured one.
    structural = strategy.has_section_headers(text)

    chunks: List[IdentifiedChunk] = []
    for order, piece in enumerate(raw):
        content = (piece.get("content") or "").strip()
        if not content:
            continue
        chunk_type = piece.get("chunk_type", "section")
        heading, _body = split_heading(content)
        chunks.append(IdentifiedChunk(
            order=order,
            hash=chunk_hash(content),
            content=content,
            heading=heading,
            chunk_type=chunk_type,
        ))

    if not structural and chunks:
        logger.warning(
            "No section pattern matched; chunk boundaries are greedy, so a single "
            "insertion will invalidate every hash after it. Chunk identity for this "
            "document is unstable."
        )

    return ChunkedDocument(chunks=chunks, profile=profile,
                           boundaries_are_structural=structural)


def reconstructs(document: ChunkedDocument, text: str) -> bool:
    """Whether the chunks, joined in order, are the document again.

    The cheapest possible check that nothing was dropped, duplicated or
    contaminated by overlap — compared in canonical form, because the joining
    whitespace is not part of what a chunk is.
    """
    joined = canonical(" ".join(c.content for c in document.chunks))
    return joined == canonical(text)
