"""Chunk identity, and the property that makes it worth having: **locality**.

A chunk hash is only useful if editing one paragraph changes one hash. If a
later change swaps in a greedy token packer, or puts the section number back
into the hashed body, identity still "works" — every chunk still gets a hash —
while quietly costing a full re-embed on every upload and making the Increment 9
diff useless. Nothing would fail. So the locality tests below are the regression
guard, and they are the point of this file.

All offline: no stack, no keys, no database.
"""
import pytest

from backend.domain.chunking import (
    CHUNKER_VERSION,
    MATCH_THRESHOLD,
    NORMALISER_VERSION,
    ChunkingProfile,
    MatchCandidate,
    canonical,
    chunk_hash,
    chunk_key,
    rarity_weight,
    split_heading,
)


class TestCanonicalFormAbsorbsWhatMeansNothing:
    """The differences a PDF re-export introduces on text nobody touched."""

    def test_whitespace_runs_and_line_breaks_collapse(self):
        assert canonical("Payment  within\n\n  thirty   days") == canonical(
            "Payment within thirty days")

    def test_a_word_broken_across_a_line_is_rejoined(self):
        """"indemni-\\nfication" is one word and a page-layout artefact."""
        assert canonical("the indemni-\nfication clause") == canonical(
            "the indemnification clause")

    def test_smart_quotes_and_dashes_fold_to_ascii(self):
        assert canonical("the “Provider’s” fee — net 30") == canonical(
            'the "Provider\'s" fee - net 30')

    def test_ligatures_normalise(self):
        """NFKC: extractors disagree about ﬁ versus f-i for the same glyph."""
        assert canonical("the ﬁnal oﬀer") == canonical("the final offer")

    def test_invisible_characters_are_removed(self):
        assert canonical("Con­fidential​ Information") == canonical(
            "Confidential Information")

    def test_case_is_folded(self):
        assert canonical("PAYMENT TERMS") == canonical("Payment Terms")

    def test_nothing_and_empty_are_the_same(self):
        assert canonical(None) == canonical("") == ""


class TestCanonicalFormKeepsWhatMeansSomething:
    """Normalising too much is worse than normalising too little.

    Re-embedding a chunk costs 210ms. Two genuinely different clauses sharing a
    hash costs a reviewer the difference between them.
    """

    def test_numbers_are_not_touched(self):
        assert canonical("within thirty (30) days") != canonical("within sixty (60) days")

    def test_a_single_digit_is_a_different_clause(self):
        assert canonical("thirty (30) days") != canonical("thirty (31) days")

    def test_negation_survives(self):
        assert canonical("Provider shall not be liable") != canonical(
            "Provider shall be liable")

    def test_word_order_survives(self):
        assert canonical("Provider indemnifies Customer") != canonical(
            "Customer indemnifies Provider")

    def test_meaningful_punctuation_survives(self):
        assert canonical("fees, and expenses") != canonical("fees and expenses")


class TestHeadingsAreNotPartOfIdentity:
    """Inserting a section renumbers every heading after it.

    With the number in the hash, one insertion invalidates the whole rest of the
    document: measured at 18 of 37 chunks surviving, against 30 of 37 without.
    """

    def test_the_same_clause_under_a_different_number_is_the_same_clause(self):
        assert chunk_hash("12. Payment. Net 30 days.") == chunk_hash(
            "13. Payment. Net 30 days.")

    def test_a_renumbered_subsection_too(self):
        assert chunk_hash("4.2 Payment Terms. Net 30.") == chunk_hash(
            "5.2 Payment Terms. Net 30.")

    @pytest.mark.parametrize("text,heading", [
        ("12. Payment Terms. Net 30.", "12."),
        ("12.3 Payment Terms are net 30.", "12.3"),
        ("ARTICLE IV - INDEMNITY. The parties agree.", "ARTICLE IV -"),
        ("Section 7: Confidentiality applies.", "Section 7:"),
        ("(a) The parties agree.", "(a)"),
        ("(iii) Subject to the above.", "(iii)"),
    ])
    def test_the_forms_that_are_headings(self, text, heading):
        assert split_heading(text)[0] == heading

    @pytest.mark.parametrize("text", [
        "The Provider shall indemnify the Customer.",
        "10115 Main Street is the registered address.",
        "$2,500 is payable monthly in arrears.",
        "1.5 million dollars in aggregate.",
        "3.14159 is pi.",
    ])
    def test_the_forms_that_are_not(self, text):
        """A street number, a dollar amount and a quantity are body text.

        Stripping them would silently delete meaning from the hash.
        """
        assert split_heading(text) == ("", text)

    def test_a_chunk_that_is_only_a_heading_keeps_it(self):
        """Otherwise every heading-only chunk hashes the empty string together."""
        assert split_heading("ARTICLE IV")[1] == "ARTICLE IV"
        assert chunk_hash("ARTICLE IV") != chunk_hash("ARTICLE V")

    def test_the_heading_is_still_available_to_store(self):
        """It is kept on the membership relationship, just not in the hash."""
        heading, body = split_heading("12.3 Payment Terms. Net 30.")

        assert heading == "12.3"
        assert "Payment Terms" in body


def _document(sections):
    return "\n\n".join(sections)


#: A small contract, one string per section, so an edit can be made surgically.
SECTIONS = [
    "1. DEFINITIONS. Capitalised terms have the meanings given below.",
    "2. SERVICES. Provider shall perform the services described in Schedule A.",
    "3. FEES AND PAYMENT. Customer shall pay each invoice within ninety (90) days.",
    "4. CONFIDENTIALITY. Each party shall keep the other's information secret.",
    "5. LIABILITY. Provider's aggregate liability is unlimited.",
    "6. TERM. This Agreement continues for three (3) years.",
    "7. GOVERNING LAW. This Agreement is governed by the laws of Delaware.",
]


def _hashes(sections):
    return [chunk_hash(s) for s in sections]


class TestAnEditIsLocal:
    """The regression guard.

    If anyone swaps in a greedy packer, this fails immediately instead of
    quietly doubling the embedding bill on every upload for ever.
    """

    def test_changing_one_paragraph_moves_one_hash(self):
        before = _hashes(SECTIONS)
        edited = list(SECTIONS)
        edited[2] = edited[2].replace("ninety (90)", "thirty (30)")

        after = _hashes(edited)

        moved = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert moved == [2], f"editing section 3 moved chunks {moved}"

    def test_the_surrounding_sections_are_untouched(self):
        before = _hashes(SECTIONS)
        edited = list(SECTIONS)
        edited[4] = edited[4].replace("unlimited", "capped at the fees paid")

        after = _hashes(edited)

        assert before[:4] == after[:4]
        assert before[5:] == after[5:]


class TestAnInsertionIsLocal:
    """The case heading-stripping exists for."""

    def test_inserting_a_section_leaves_every_other_hash_alone(self):
        before = _hashes(SECTIONS)
        with_new = SECTIONS[:3] + [
            "4. INSURANCE. Provider shall maintain commercial general liability cover."
        ] + SECTIONS[3:]

        # Renumber everything after the insertion, as a real document would.
        renumbered = []
        for index, section in enumerate(with_new, start=1):
            renumbered.append(f"{index}." + section.split(".", 1)[1])

        after = _hashes(renumbered)

        survived = [h for h in before if h in after]
        assert len(survived) == len(before), (
            f"only {len(survived)}/{len(before)} chunks survived an insertion — "
            "the section number is back in the hash"
        )

    def test_and_the_new_section_is_the_only_new_hash(self):
        before = set(_hashes(SECTIONS))
        with_new = SECTIONS[:3] + [
            "4. INSURANCE. Provider shall maintain commercial general liability cover."
        ] + SECTIONS[3:]
        renumbered = [f"{i}." + s.split(".", 1)[1] for i, s in enumerate(with_new, start=1)]

        after = set(_hashes(renumbered))

        assert len(after - before) == 1


class TestGoldenHashes:
    """Pinned literals, so the definition cannot drift unnoticed.

    Changing what a hash is over invalidates every stored hash at once — version
    membership, match results and embedding reuse all become meaningless. If one
    of these fails, that is what happened: bump `CHUNKER_VERSION` or
    `NORMALISER_VERSION` and re-pin, so the mismatch is visible in the data
    instead of silently comparing incomparable things.

    Literals rather than recomputed values, which is the whole point — a golden
    test that calls the function to work out what it expects cannot fail.
    """

    GOLDEN = {
        "3. FEES AND PAYMENT. Customer shall pay each invoice within ninety (90) days.":
            "7681e324b2f85e082ee9ffab92e39ab04fde819911ec569680506635ff3861db",
        "(a) Each party shall keep the other party's Confidential Information secret.":
            "b70a495cacac598aea29325f0fb784645a9b8e603fc656e8017bc0f70ce3fca8",
        "The Provider shall indemnify the Customer against third-party claims.":
            "2ddc4cd60998e43e11fab1914733980e91a5e4e343672dc63a7c57a13256ddd9",
    }

    def test_the_versions_are_declared(self):
        assert NORMALISER_VERSION >= 1
        assert CHUNKER_VERSION >= 1

    @pytest.mark.parametrize("text", list(GOLDEN))
    def test_the_hash_of_a_known_clause_does_not_move(self, text):
        assert chunk_hash(text) == self.GOLDEN[text], (
            "the hash definition changed — bump CHUNKER_VERSION / NORMALISER_VERSION "
            "and re-pin, so stored hashes are known to be incomparable"
        )

    def test_the_same_text_hashes_the_same_every_time(self):
        text = list(self.GOLDEN)[0]

        assert chunk_hash(text) == chunk_hash(text)


class TestChunksAreScopedToTheirTenant:
    def test_the_merge_key_carries_the_tenant(self):
        """A global key collapses two customers' identical boilerplate onto one
        node: a tenancy violation, and a GDPR deletion that destroys someone
        else's version."""
        assert chunk_key("acme", "abc") != chunk_key("globex", "abc")

    def test_the_same_tenant_and_text_is_the_same_key(self):
        assert chunk_key("acme", "abc") == chunk_key("acme", "abc")


class TestTheProfileIsRecordedSoLaterVersionsMatch:
    def test_overlap_is_zero(self):
        """Overlap made a chunk's identity depend on its neighbour."""
        assert ChunkingProfile().overlap == 0

    def test_it_round_trips(self):
        profile = ChunkingProfile(strategy="section", extractor="pdfplumber")

        assert ChunkingProfile.from_dict(profile.to_dict()) == profile

    def test_a_profile_from_a_newer_build_does_not_crash_an_older_one(self):
        stored = {**ChunkingProfile().to_dict(), "something_new": 42}

        assert ChunkingProfile.from_dict(stored).strategy == "section"

    def test_nothing_stored_yields_the_default(self):
        assert ChunkingProfile.from_dict(None) == ChunkingProfile()

    def test_a_stale_profile_is_visible_rather_than_silent(self):
        assert ChunkingProfile().is_current
        assert not ChunkingProfile(chunker_version=0).is_current
        assert not ChunkingProfile(normaliser_version=0).is_current


class TestTheAdvisoryMatchNeedsBothDirections:
    """One number would call an SOW a new round of the MSA it quotes."""

    def test_a_genuine_new_round_scores_high_both_ways(self):
        candidate = MatchCandidate("MSA-2026-0042", shared=95, new_total=100,
                                   candidate_total=100)

        assert candidate.forward >= MATCH_THRESHOLD
        assert candidate.backward >= MATCH_THRESHOLD
        assert candidate.score >= MATCH_THRESHOLD

    def test_a_small_document_quoting_a_large_one_does_not_match(self):
        """Every chunk of the SOW appears in the MSA; it is not a new round."""
        candidate = MatchCandidate("MSA-2026-0042", shared=20, new_total=20,
                                   candidate_total=200)

        assert candidate.forward == 1.0
        assert candidate.backward == 0.1
        assert candidate.score < MATCH_THRESHOLD, "one direction carried the match"

    def test_a_large_document_containing_a_small_one_does_not_either(self):
        candidate = MatchCandidate("SOW-2026-0001", shared=20, new_total=200,
                                   candidate_total=20)

        assert candidate.score < MATCH_THRESHOLD

    def test_nothing_shared_is_not_a_division_by_zero(self):
        assert MatchCandidate("X", shared=0, new_total=0, candidate_total=0).score == 0.0


class TestBoilerplateBarelyCounts:
    def test_a_clause_unique_to_two_documents_carries_full_weight(self):
        assert rarity_weight(1) == 1.0

    def test_a_clause_in_fifty_documents_carries_almost_none(self):
        assert rarity_weight(50) < 0.3

    def test_rarity_falls_as_the_clause_spreads(self):
        weights = [rarity_weight(df) for df in (1, 2, 5, 20, 100)]

        assert weights == sorted(weights, reverse=True)

    def test_a_missing_count_does_not_divide_by_zero(self):
        assert rarity_weight(0) == 1.0


# ==========================================================================
# Through the real chunker, not hand-split sections
# ==========================================================================

from backend.infrastructure.chunking.identity import identify_chunks, reconstructs

CONTRACT = "\n\n".join([
    "1. DEFINITIONS. Capitalised terms have the meanings given in this Section.",
    "2. SERVICES. Provider shall perform the services described in Schedule A.",
    "3. FEES AND PAYMENT. Customer shall pay each invoice within ninety (90) days.",
    "4. CONFIDENTIALITY. Each party shall keep the other's information secret.",
    "5. LIABILITY. Provider's aggregate liability under this Agreement is unlimited.",
    "6. TERM. This Agreement continues for three (3) years from the Effective Date.",
    "7. GOVERNING LAW. This Agreement is governed by the laws of Delaware.",
])


def _renumber(sections):
    """Renumber sections 1..n, as inserting one into a real document would."""
    return [f"{i}." + s.split(".", 1)[1] for i, s in enumerate(sections, start=1)]


class TestTheRealChunkerKeepsEditsLocal:
    """`test_an_edit_is_local` above hashes hand-split sections. This drives the
    actual `SectionStrategy`, so a change to *boundary placement* is caught too —
    the greedy-packer regression the increment exists to prevent."""

    def test_one_edited_paragraph_moves_one_chunk(self):
        before = identify_chunks(CONTRACT)
        edited = CONTRACT.replace("ninety (90)", "thirty (30)")

        after = identify_chunks(edited)

        assert len(before.chunks) == len(after.chunks)
        moved = [i for i, (a, b) in enumerate(zip(before.hashes, after.hashes)) if a != b]
        assert moved == [2], f"editing one section moved chunks {moved}"

    def test_inserting_a_section_leaves_the_others_alone(self):
        sections = CONTRACT.split("\n\n")
        before = identify_chunks(CONTRACT)

        with_new = sections[:3] + [
            "4. INSURANCE. Provider shall maintain commercial general liability cover."
        ] + sections[3:]
        after = identify_chunks("\n\n".join(_renumber(with_new)))

        survived = before.distinct_hashes & after.distinct_hashes
        assert len(survived) == len(before.chunks), (
            f"only {len(survived)}/{len(before.chunks)} chunks survived an insertion"
        )

    def test_only_the_inserted_section_is_new(self):
        sections = CONTRACT.split("\n\n")
        before = identify_chunks(CONTRACT)
        with_new = sections[:3] + [
            "4. INSURANCE. Provider shall maintain commercial general liability cover."
        ] + sections[3:]

        after = identify_chunks("\n\n".join(_renumber(with_new)))

        assert len(after.distinct_hashes - before.distinct_hashes) == 1

    def test_an_unchanged_document_costs_no_embeddings_at_all(self):
        """The reuse claim, stated as a test."""
        first = identify_chunks(CONTRACT)
        again = identify_chunks(CONTRACT)

        assert first.hashes == again.hashes
        assert not (again.distinct_hashes - first.distinct_hashes)


class TestNothingIsDroppedOrDuplicated:
    """Catches overlap contamination and lost text outright."""

    def test_the_chunks_rejoin_into_the_document(self):
        assert reconstructs(identify_chunks(CONTRACT), CONTRACT)

    def test_no_chunk_repeats_its_neighbour(self):
        """Overlap used to prepend 20% of each chunk onto the next."""
        chunks = identify_chunks(CONTRACT).chunks

        for earlier, later in zip(chunks, chunks[1:]):
            tail = canonical(earlier.content)[-40:]
            assert tail and tail not in canonical(later.content)

    def test_the_order_is_dense_and_ascending(self):
        chunks = identify_chunks(CONTRACT).chunks

        assert [c.order for c in chunks] == list(range(len(chunks)))

    def test_a_blank_piece_does_not_leave_a_gap_in_the_order(self):
        """`order` is the key the membership list is MERGEd on and the key the
        "join neighbours in order" retrieval plan walks, so a hole at position 2
        is a trap for anything checking adjacency."""
        with_blanks = CONTRACT.replace(
            "3. FEES AND PAYMENT.", "\n\n   \n\n3. FEES AND PAYMENT.")

        orders = [c.order for c in identify_chunks(with_blanks).chunks]

        assert orders == list(range(len(orders))), f"gap in the order: {orders}"

    def test_an_empty_document_is_no_chunks_rather_than_a_crash(self):
        assert identify_chunks("").chunks == []
        assert identify_chunks("   \n  ").chunks == []


class TestAnUnstructuredDocumentSaysSo:
    """A greedy packer has no boundary locality: one insertion shifts every
    boundary after it and invalidates every hash downstream. Silently, unless
    something says otherwise."""

    def test_a_sectioned_contract_has_structural_boundaries(self):
        assert identify_chunks(CONTRACT).boundaries_are_structural

    def test_a_bare_all_caps_title_is_not_a_section(self):
        """"CONFIDENTIALITY AGREEMENT" matches the same pattern a genuine
        ALL-CAPS heading does, so a single hit let a document with no sections
        at all claim anchored boundaries — and then claim stable chunk identity
        it does not have."""
        prose = " ".join(
            "The parties acknowledge that this arrangement is mutually beneficial."
            for _ in range(60)
        )

        for title in ("CONFIDENTIALITY AGREEMENT", "MUTUAL UNDERTAKING OF CONFIDENCE"):
            document = identify_chunks(f"{title}\n\n{prose}")
            assert not document.boundaries_are_structural, title

    def test_one_section_anchor_is_not_enough(self):
        from backend.infrastructure.chunking.section_strategy import SectionStrategy

        assert SectionStrategy.MIN_SECTION_ANCHORS >= 2

    def test_a_genuinely_sectioned_contract_still_counts(self):
        assert identify_chunks(CONTRACT).boundaries_are_structural

    def test_prose_with_no_headings_is_flagged(self):
        prose = " ".join(
            "The parties acknowledge that this arrangement is mutually beneficial "
            "and shall endure for the duration described elsewhere herein."
            for _ in range(80)
        )

        document = identify_chunks(prose)

        assert document.chunks
        assert not document.boundaries_are_structural, (
            "an unstructured document must not claim stable chunk identity"
        )


class TestTheProfilePinsTheChunker:
    def test_the_same_profile_gives_the_same_boundaries(self):
        profile = ChunkingProfile(max_chunk_size=1200)

        assert (identify_chunks(CONTRACT, profile).hashes
                == identify_chunks(CONTRACT, profile).hashes)

    def test_a_different_size_is_a_different_chunking(self):
        """Which is exactly why the profile is recorded and reused.

        A section longer than `max_chunk_size` is subdivided, and where the
        subdivisions fall depends on the size — so two versions chunked under
        different profiles share nothing through that section.
        """
        long_section = "3. FEES AND PAYMENT.\n\n" + "\n\n".join(
            f"Paragraph {n} of the payment terms, stating an obligation at some length "
            f"so that the section as a whole exceeds the smaller chunk size." 
            for n in range(12)
        )

        small = identify_chunks(long_section, ChunkingProfile(max_chunk_size=400))
        large = identify_chunks(long_section, ChunkingProfile(max_chunk_size=8000))

        assert len(small.chunks) > len(large.chunks)
        assert small.hashes != large.hashes

    def test_a_later_round_inherits_the_boundaries_but_not_the_stamps(self):
        """Copying the version stamps forward destroys the mismatch
        `is_current` exists to surface, at the moment it matters: after a bump,
        round 2 is hashed by the new rules, stamped with the old ones, shares no
        chunk with round 1, and reports zero reuse with nothing to explain it."""
        old_profile = ChunkingProfile(
            strategy="section", max_chunk_size=1800, extractor="pypdf",
            chunker_version=0, normaliser_version=0,
        )

        reused = old_profile.reused_for("pdfplumber")

        assert reused.max_chunk_size == 1800, "the boundary settings must carry over"
        assert reused.strategy == "section"
        assert reused.extractor == "pdfplumber", "this round's extractor, not v1's"
        assert reused.is_current, "the stamps must describe the rules actually used"

    def test_an_unknown_extractor_does_not_overwrite_a_known_one(self):
        profile = ChunkingProfile(extractor="pypdf")

        assert profile.reused_for("").extractor == "pypdf"

    def test_the_profile_comes_back_with_the_document(self):
        profile = ChunkingProfile(extractor="pdfplumber")

        assert identify_chunks(CONTRACT, profile).profile == profile
