"""Chunk identity: what a chunk *is*, independent of which upload produced it.

Embedding a chunk is one network call — measured at ~210ms — so a 200-chunk
contract costs ~42s, and today every upload pays that in full even when one
paragraph changed. A chunk addressed by its content is embedded once and
referenced thereafter.

Reuse is the cheaper half of the reason. The dearer half is that **two versions
of a contract cannot be compared at all** until chunks have stable identities:
"what moved since last round" is a set difference over hashes, and a hash that
changes when nothing changed makes that question unanswerable.

Everything that decides *what the hash is* lives here, and lands together on
purpose. Changing the definition later invalidates every stored hash at once —
version membership, match results and embedding reuse all become meaningless —
and recovering costs a migration that re-hashes and re-embeds everything. So
heading-stripping and overlap-removal are in this module's remit, not a later
increment's.

No I/O, so every rule in it is testable with the database down.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

#: Bumped when `canonical()` changes what it produces. Stored on every version,
#: so a hash can always be traced to the rules that made it, and a mismatch is
#: visible rather than silently comparing incomparable things.
NORMALISER_VERSION = 1

#: Bumped when chunk *boundaries* change — a different splitter, different
#: sizes, a different heading rule. The golden-hash test fails until it is.
CHUNKER_VERSION = 1


# --------------------------------------------------------------------------
# Canonical form
# --------------------------------------------------------------------------

#: Soft hyphen, zero-width space/non-joiner/joiner, word joiner, BOM. PDF
#: extractors sprinkle these through text and no two agree on where.
_INVISIBLES = dict.fromkeys(map(ord, "­​‌‍⁠﻿"))

#: Curly quotes, dashes and ellipsis folded to ASCII. A Word re-export flips
#: these on text nobody edited.
_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
})

#: A word broken across a line break: "indemni-\nfication". Joined, because the
#: break is a property of the page layout, not of the text.
_DEHYPHENATE = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")

_WHITESPACE = re.compile(r"\s+")


def canonical(text: Optional[str]) -> str:
    """The form a hash is taken over.

    Absorbs the differences that mean nothing — the ones a PDF re-export
    introduces on text nobody touched. Ligatures and compatibility forms (NFKC),
    invisible characters, hyphenation across line breaks, smart quotes and
    dashes, runs of whitespace, and case.

    Deliberately *not* absorbed: punctuation that carries meaning, numbers, and
    word order. Normalising those would make two genuinely different clauses
    hash the same, which is a far worse failure than re-embedding a chunk.

    Casefolding is the one judgement call. A clause retyped in a different case
    is the same clause; a clause that changed case *deliberately* — defined
    terms, "Confidential Information" vs "confidential information" — is not.
    Case is folded because PDF extraction of small-caps and heading styles is
    not reliable enough to treat a case difference as a real edit.
    """
    if not text:
        return ""

    # NFKC first: it decomposes ligatures (ﬁ -> fi) and normalises the
    # compatibility forms different extractors emit for the same glyph.
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_INVISIBLES)
    text = text.translate(_PUNCTUATION)
    text = _DEHYPHENATE.sub(r"\1\2", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip().casefold()


# --------------------------------------------------------------------------
# Headings
# --------------------------------------------------------------------------

#: A leading section number, anchored at the start. `12.`, `12.3`, `(a)`,
#: `ARTICLE IV`, `Section 7 -`.
_HEADING = re.compile(
    r"""^\s*(?:
        # "ARTICLE IV", "Section 7 -", "Schedule 2:"
        (?:article|section|clause|schedule|exhibit|appendix|annex)\s+
            (?:[ivxlcdm]+|\d+(?:\.\d+)*)\b [.):\-–\s]*
        # "12." / "12.3)" / "4 -" — a number closed by a delimiter
      | \d+(?:\.\d+)*\s*[.):\-–]\s+
        # "12.3 Payment Terms" — a dotted number and a capitalised title.
        # The capital is what separates a heading from "1.5 million", and the
        # dot is what separates it from a street number. `(?-i:...)` because the
        # pattern is IGNORECASE overall, which would make [A-Z] match anything.
      | \d+(?:\.\d+)+\s+(?=(?-i:[A-Z]))
        # "(a)", "(iii)", "(2)"
      | \(\s*(?:[a-z]{1,2}|\d{1,3}|[ivxlcdm]{1,6})\s*\)\s*
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def split_heading(text: str) -> tuple[str, str]:
    """Separate a chunk's leading section number from its body.

    The number is kept — on the membership relationship, not in the hash. It
    has to be *out* of the hash because inserting a section renumbers every
    heading after it: with the number in, one insertion invalidates the entire
    rest of the document. Measured on a 24-section contract, inserting a new
    Section 4 leaves 18 of 37 chunks stable with the number hashed, and 30 of 37
    without.

    Anchored at the start on purpose. A section number *inside* a chunk body is
    left alone, because telling `13.` from a dollar amount, a statute citation
    or a cross-reference mid-sentence is real work for a small payoff — measured
    at one residual chunk. Left deliberately; see the increment's known limits.
    """
    if not text:
        return "", ""
    match = _HEADING.match(text)
    if not match:
        return "", text
    heading = text[: match.end()].strip()
    body = text[match.end():]
    # A chunk that is *only* a heading has no body to hash; keep it whole rather
    # than reducing it to the empty string, which every such chunk would share.
    if not body.strip():
        return "", text
    return heading, body


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def chunk_hash(text: str, *, strip_heading: bool = True) -> str:
    """A chunk's identity: SHA-256 over its canonical body.

    Not over the raw text, and not including the heading. Two chunks with the
    same hash are the same text as far as this system is concerned, and are
    embedded once between them.
    """
    body = split_heading(text)[1] if strip_heading else text
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def chunk_key(tenant_id: str, digest: str) -> str:
    """The MERGE key for a chunk node — **never the hash alone.**

    A global key would collapse identical boilerplate ("governed by the laws of
    the State of Delaware") from two customers onto one node. That is a tenancy
    violation on its face, and it makes a GDPR deletion impossible to perform:
    removing the node destroys another tenant's version.
    """
    return f"{tenant_id}|{digest}"


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ChunkingProfile:
    """How a version was chunked, recorded so later versions match it.

    Strategy selection is threshold-based scoring over section counts and
    clause density, and those thresholds can flip on a one-word edit — a
    document that chunked by `section` in v1 chunking by `paragraph` in v2 means
    every boundary moves and no chunk survives, for no reason a reader could
    ever see. So selection runs **once, for version 1**, and every later round
    of the same matter reuses what is recorded here.

    `extractor` is part of it because `extract_with_fallback` tries extractors
    in order and returns the first that yields anything — and different
    libraries produce different whitespace for the same file, which is a
    different hash for the same text.
    """

    strategy: str = "section"
    extractor: str = "unknown"
    normaliser_version: int = NORMALISER_VERSION
    chunker_version: int = CHUNKER_VERSION
    min_chunk_size: int = 500
    max_chunk_size: int = 2000
    #: Zero, and not a knob. Overlap made a chunk's identity depend on its
    #: neighbour, which is the one thing identity cannot do.
    overlap: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ChunkingProfile":
        """Read a stored profile, ignoring fields this build does not know.

        A version written by a newer build should not crash an older one; it
        should chunk with what it understands and let the version mismatch show.
        """
        if not data:
            return cls()
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    @property
    def is_current(self) -> bool:
        """Whether hashes made under this profile are comparable with new ones."""
        return (self.normaliser_version == NORMALISER_VERSION
                and self.chunker_version == CHUNKER_VERSION)

    def reused_for(self, extractor: str) -> "ChunkingProfile":
        """This profile's *boundary* settings, stamped with today's reality.

        A later round reuses the strategy and the sizes — that is the whole
        point — but it must not inherit the recorded `normaliser_version`,
        `chunker_version` or `extractor`, because those describe how version 1's
        hashes were made, and this round's were made by whatever is running now.

        Copying them forward destroys the mismatch `is_current` exists to
        surface, at exactly the moment it matters: after a version bump, round 2
        would be hashed by the new rules, stamped with the old ones, share no
        chunk with round 1, and report zero reuse with nothing to explain why.
        """
        return ChunkingProfile(
            strategy=self.strategy,
            min_chunk_size=self.min_chunk_size,
            max_chunk_size=self.max_chunk_size,
            overlap=self.overlap,
            extractor=extractor or self.extractor,
            normaliser_version=NORMALISER_VERSION,
            chunker_version=CHUNKER_VERSION,
        )


# --------------------------------------------------------------------------
# Advisory matching
# --------------------------------------------------------------------------

@dataclass
class MatchCandidate:
    """A matter that shares text with an incoming document.

    Two numbers, because one is not enough. `forward` is how much of the *new*
    document the candidate explains; `backward` is how much of the *candidate*
    the new document covers. A genuine new round scores high on both. An SOW
    that merely quotes its MSA's boilerplate scores high on forward and low on
    backward — and a single number would have called it a match.
    """

    matter_ref: str
    title: str = ""
    version: Optional[int] = None
    shared: float = 0.0
    new_total: float = 0.0
    candidate_total: float = 0.0

    @property
    def forward(self) -> float:
        return self.shared / self.new_total if self.new_total else 0.0

    @property
    def backward(self) -> float:
        return self.shared / self.candidate_total if self.candidate_total else 0.0

    @property
    def score(self) -> float:
        """The weaker of the two directions, so neither alone can carry it."""
        return min(self.forward, self.backward)


#: At or above this, the match is offered as a link with its percentage. It
#: never decides anything — it sits next to the choice the user was going to
#: make anyway, because a new SOW for a different vendor off the same template
#: is indistinguishable from a new round and only the user knows which it is.
MATCH_THRESHOLD = 0.80


def rarity_weight(document_frequency: int) -> float:
    """How much one shared chunk is worth: `1 / log(1 + df)`.

    Boilerplate appears in every contract a tenant has, so counting shared
    chunks unweighted would make every document look like a new round of every
    other. A clause present in fifty *matters* carries almost nothing; one
    present in two carries almost all of its weight.

    `df` counts **distinct matters**, never versions. A clause carried through
    four rounds of one negotiation is the strongest evidence a match could have,
    and counting versions would score it as boilerplate — so the more rounds a
    matter had, the less it would look like itself.
    """
    import math

    df = max(1, int(document_frequency or 1))
    if df == 1:
        return 1.0
    return 1.0 / math.log(1 + df)


# --------------------------------------------------------------------------
# Analysis windows
# --------------------------------------------------------------------------

#: Characters of contract text sent to the model in one extraction call.
#: Deliberately the same size as the single window this replaces, so what
#: changes is *coverage*, not per-call behaviour — the model sees the same
#: amount of text at a time, it just now sees all of it.
WINDOW_BUDGET_CHARS = 12_000


@dataclass(frozen=True)
class AnalysisWindow:
    """A run of consecutive whole chunks, sent to the model in one call.

    **Whole chunks, never split.** That is not a tidiness preference: because a
    window is a set of chunk identities, every finding maps back to the specific
    chunks it came from, which is what makes Increment 9's incremental
    re-analysis possible — re-analyse the windows whose chunks changed, keep the
    rest. Splitting a chunk across two windows would break that mapping and
    invent a second structure alongside the `INCLUDES` membership list.
    """

    index: int
    chunk_hashes: tuple
    text: str
    first_order: int
    last_order: int

    @property
    def size(self) -> int:
        return len(self.text)


def pack_windows(chunks: List[Any], budget: int = WINDOW_BUDGET_CHARS,
                 separator: str = "\n\n") -> List[AnalysisWindow]:
    """Pack consecutive chunks into windows no larger than `budget`.

    The alternative — one call per chunk — is unaffordable: the Shell MESA is
    about 200 chunks, so 200 model calls for one review. Packing turns 313,000
    characters into roughly 26.

    A single chunk larger than the budget gets a window of its own rather than
    being split, because splitting it would cost the chunk-to-finding mapping
    for the sake of a limit the model will usually tolerate anyway.
    """
    windows: List[AnalysisWindow] = []
    current: List[Any] = []
    current_size = 0

    def flush() -> None:
        if not current:
            return
        windows.append(AnalysisWindow(
            index=len(windows),
            chunk_hashes=tuple(c.hash for c in current),
            text=separator.join(c.content for c in current),
            first_order=current[0].order,
            last_order=current[-1].order,
        ))

    for chunk in chunks:
        addition = len(chunk.content) + (len(separator) if current else 0)
        if current and current_size + addition > budget:
            flush()
            current, current_size = [], 0
            addition = len(chunk.content)
        current.append(chunk)
        current_size += addition

    flush()
    return windows
