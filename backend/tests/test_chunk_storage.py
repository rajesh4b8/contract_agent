"""Chunks as shared nodes: reuse, tenancy, membership and retention.

The claim this increment makes is that a new round of a contract costs one
embedding per *changed* paragraph rather than one per paragraph. These tests
pin the parts of that claim which are easy to break silently — a reused vector
from the wrong model, a membership list that grows instead of being replaced,
and a tidy-up that deletes text somebody still needs.

Offline: the graph is injected, so the statements can be read directly.
"""
import pytest

from backend.domain.chunking import ChunkingProfile
from backend.infrastructure.chunk_repository import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    ChunkRepository,
    _flatten,
    _unflatten,
)
from backend.infrastructure.chunking.identity import identify_chunks

CONTRACT = "\n\n".join([
    "1. DEFINITIONS. Capitalised terms have the meanings given in this Section.",
    "2. SERVICES. Provider shall perform the services described in Schedule A.",
    "3. FEES AND PAYMENT. Customer shall pay each invoice within ninety (90) days.",
    "4. CONFIDENTIALITY. Each party shall keep the other's information secret.",
])


class FakeGraph:
    """Records statements and answers with canned rows."""

    def __init__(self, *responses):
        self.calls = []
        self.responses = list(responses)

    def query(self, statement, params=None):
        self.calls.append((statement, params or {}))
        if not self.responses:
            return []
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def statements(self):
        return [s for s, _ in self.calls]

    @property
    def params(self):
        return [p for _, p in self.calls]


class CountingEmbedder:
    def __init__(self):
        self.texts = []

    def __call__(self, texts):
        self.texts.extend(texts)
        return [[0.5] * EMBEDDING_DIMENSIONS for _ in texts]


def repo(*responses, embedder=None):
    return ChunkRepository(FakeGraph(*responses), embedder=embedder or CountingEmbedder())


#: What the store statement returns when it actually attached something. An
#: empty result means the version did not exist and nothing was written, which
#: the repository now reports rather than claiming success.
ATTACHED = [{"attached": 14}]


class TestUnchangedTextIsNeverReEmbedded:
    """~210ms per chunk. A 200-chunk contract is ~42s of it."""

    def test_a_first_upload_embeds_everything(self):
        embedder = CountingEmbedder()
        document = identify_chunks(CONTRACT)
        repository = repo([], ATTACHED, embedder=embedder)

        result = repository.store_version_chunks("acme", "V-1", document)

        assert result["embedded"] == len(document.chunks)
        assert result["reused"] == 0
        assert len(embedder.texts) == len(document.chunks)

    def test_chunks_the_tenant_already_holds_are_not_embedded_again(self):
        embedder = CountingEmbedder()
        document = identify_chunks(CONTRACT)
        known = [{"hash": h} for h in document.hashes[:3]]
        repository = repo(known, ATTACHED, embedder=embedder)

        result = repository.store_version_chunks("acme", "V-2", document)

        assert result["reused"] == 3
        assert result["embedded"] == len(document.chunks) - 3
        assert len(embedder.texts) == len(document.chunks) - 3

    def test_an_unchanged_document_costs_nothing(self):
        embedder = CountingEmbedder()
        document = identify_chunks(CONTRACT)
        repository = repo([{"hash": h} for h in document.hashes], ATTACHED, embedder=embedder)

        result = repository.store_version_chunks("acme", "V-2", document)

        assert result["embedded"] == 0
        assert embedder.texts == []

    def test_a_repeated_paragraph_is_embedded_once(self):
        embedder = CountingEmbedder()
        document = identify_chunks(CONTRACT + "\n\n" + CONTRACT.split("\n\n")[1])
        repository = repo([], ATTACHED, embedder=embedder)

        repository.store_version_chunks("acme", "V-1", document)

        assert len(embedder.texts) == len(set(document.hashes))


class TestAReusedVectorMustBeComparable:
    """Serving a vector from a different model in the same similarity search is
    worse than paying to redo it — the numbers are not on the same scale."""

    def test_the_lookup_is_guarded_on_the_model_and_dimensions(self):
        repository = repo([])

        repository.existing_hashes("acme", ["abc"])

        statement, params = repository.graph.calls[0]
        assert "c.embedding_model = $model" in statement
        assert "c.embedding_dimensions = $dimensions" in statement
        assert params["model"] == EMBEDDING_MODEL
        assert params["dimensions"] == EMBEDDING_DIMENSIONS

    def test_a_chunk_with_no_vector_does_not_count_as_reusable(self):
        repository = repo([])

        repository.existing_hashes("acme", ["abc"])

        assert "c.embedding IS NOT NULL" in repository.graph.statements[0]

    def test_nothing_asked_is_nothing_queried(self):
        repository = repo()

        assert repository.existing_hashes("acme", []) == set()
        assert repository.graph.calls == []


class TestChunksBelongToOneTenant:
    def test_the_merge_key_is_the_tenant_and_the_hash(self):
        """A global key collapses two customers' identical boilerplate onto one
        node — a tenancy violation, and a GDPR deletion that destroys someone
        else's version."""
        repository = repo([], ATTACHED)

        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        write = [s for s in repository.graph.statements if "MERGE (c:Chunk" in s][0]
        assert "MERGE (c:Chunk {tenant_id: $tenant_id, hash: row.hash})" in write

    def test_every_statement_is_tenant_scoped(self):
        repository = repo([], [], ATTACHED, [])
        repository.existing_hashes("acme", ["a"])
        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        for statement, params in repository.graph.calls:
            assert params.get("tenant_id") == "acme", statement


class TestMembershipIsReplacedNotAccumulated:
    def test_re_storing_a_version_drops_its_previous_list(self):
        repository = repo([], ATTACHED)

        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        write = [s for s in repository.graph.statements if "MERGE (c:Chunk" in s][0]
        assert "OPTIONAL MATCH (v)-[old:INCLUDES]->(:Chunk)" in write
        assert "DELETE old" in write

    def test_but_the_chunks_themselves_are_left_alone(self):
        """They are shared. Deleting one because this version stopped
        referencing it would delete another version's text."""
        repository = repo([], ATTACHED)

        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        write = [s for s in repository.graph.statements if "MERGE (c:Chunk" in s][0]
        assert "DETACH DELETE" not in write
        assert "DELETE c" not in write

    def test_the_order_and_heading_ride_on_the_relationship(self):
        repository = repo([], ATTACHED)

        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        write = [s for s in repository.graph.statements if "MERGE (c:Chunk" in s][0]
        assert "MERGE (v)-[i:INCLUDES {order: row.order}]->(c)" in write
        assert "SET i.heading = row.heading" in write

    def test_a_reused_chunk_is_not_handed_a_new_vector(self):
        document = identify_chunks(CONTRACT)
        repository = repo([{"hash": h} for h in document.hashes], ATTACHED)

        repository.store_version_chunks("acme", "V-2", document)

        rows = [p for s, p in repository.graph.calls if "rows" in p][0]["rows"]
        assert all(row["embedding"] is None for row in rows)


class TestAFailedEmbeddingStillKeepsTheVersionsStructure:
    def test_the_chunks_are_stored_without_vectors(self):
        document = identify_chunks(CONTRACT)
        repository = repo([], ATTACHED, embedder=_raising)

        result = repository.store_version_chunks("acme", "V-1", document)

        assert result["embedded"] == 0
        assert result["failed"] == len(document.chunks)
        assert any("MERGE (c:Chunk" in s for s in repository.graph.statements), (
            "losing the membership list would lose the version's structure too"
        )

    def test_and_it_does_not_raise(self):
        repository = repo([], ATTACHED, embedder=_raising)

        repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))


def _raising(texts):
    raise RuntimeError("the embedding provider is down")


class TestTheProfileIsRecordedAndReused:
    def test_it_is_stored_flattened(self):
        """Neo4j properties cannot hold maps."""
        repository = repo([])

        repository.set_profile("acme", "V-1", ChunkingProfile(extractor="pdfplumber"))

        stored = repository.graph.params[0]["profile"]
        assert all(isinstance(entry, str) for entry in stored)
        assert "extractor=pdfplumber" in stored

    def test_it_round_trips(self):
        profile = ChunkingProfile(extractor="pypdf", max_chunk_size=1800)

        assert ChunkingProfile.from_dict(_unflatten(_flatten(profile))) == profile

    def test_a_later_round_reads_version_ones_profile(self):
        """Re-running strategy selection lets a one-word edit flip a document
        from section to paragraph chunking, and then no chunk survives."""
        repository = repo([{"profile": _flatten(ChunkingProfile(extractor="pdfplumber"))}])

        profile = repository.profile_for_matter("acme", "MSA-2026-0042")

        assert profile.extractor == "pdfplumber"
        assert "ORDER BY r.n" in repository.graph.statements[0], "not the first version"

    def test_a_matter_with_no_recorded_profile_is_none(self):
        assert repo([]).profile_for_matter("acme", "MSA-2026-0042") is None


class TestTheAdvisoryMatch:
    def test_it_looks_up_the_new_documents_hashes_rather_than_scanning(self):
        """O(chunks in the new document), not O(chunks in the tenant)."""
        repository = repo([])

        repository.find_matches("acme", ["a", "b", "c"])

        statement, params = repository.graph.calls[0]
        assert statement.strip().startswith("UNWIND $hashes AS h")
        assert params["hashes"] == ["a", "b", "c"]

    def test_closed_matters_are_not_offered(self):
        repository = repo([])
        repository.find_matches("acme", ["a"])

        assert "m.status <> 'CLOSED'" in repository.graph.statements[0]

    def test_the_incoming_version_does_not_match_itself(self):
        repository = repo([])
        repository.find_matches("acme", ["a"], exclude_version="V-1")

        assert repository.graph.params[0]["exclude"] == "V-1"

    def test_nothing_shared_is_no_query_at_all(self):
        repository = repo()

        assert repository.find_matches("acme", []) == []
        assert repository.graph.calls == []

    def test_a_strong_match_is_returned(self):
        repository = repo(
            [{"matter_ref": "MSA-2026-0042", "title": "Acme MSA", "version_id": "V-1",
              "shared": [{"hash": h, "df": 1} for h in ("a", "b", "c", "d")]}],
            [{"version_id": "V-1", "dfs": [1, 1, 1, 1]}],
        )

        matches = repository.find_matches("acme", ["a", "b", "c", "d"])

        assert [m.matter_ref for m in matches] == ["MSA-2026-0042"]
        assert matches[0].score == pytest.approx(1.0)

    def test_a_weak_match_is_not(self):
        """Two chunks of a twenty-chunk matter is not a new round of it."""
        repository = repo(
            [{"matter_ref": "MSA-2026-0042", "title": "Acme MSA", "version_id": "V-1",
              "shared": [{"hash": h, "df": 1} for h in ("a", "b")]}],
            [{"version_id": "V-1", "dfs": [1] * 20}],
        )

        assert repository.find_matches("acme", ["a", "b"] + [f"x{i}" for i in range(18)]) == []

    def test_document_frequency_counts_matters_not_versions(self):
        """A clause carried through four rounds of one negotiation is the
        strongest evidence a match could have. Counting versions scores it as
        boilerplate, so the more rounds a matter has the less it looks like
        itself — observed live: a lookalike sharing 12 of 14 chunks with a
        three-round matter scored 79% and fell below the threshold."""
        repository = repo([])

        repository.find_matches("acme", ["a"])

        statement = repository.graph.statements[0]
        assert "RETURN DISTINCT other" in statement, "df is not counting distinct matters"
        assert "HAS_VERSION]-(other:Matter)" in statement

    def test_a_repeated_chunk_is_counted_once_in_the_backward_direction(self):
        """`shared` is built from distinct hashes, so the candidate's total must
        be too — otherwise a version containing the same chunk twice understates
        `backward` and a real new round loses its suggestion."""
        repository = repo(
            [{"matter_ref": "M", "title": "", "version_id": "V-1",
              "shared": [{"hash": "a", "df": 1}]}],
            [],
        )

        repository.find_matches("acme", ["a"])

        assert "WITH DISTINCT v, c" in repository.graph.statements[1]

    def test_the_backward_direction_uses_the_same_definition(self):
        """Otherwise forward and backward are measured on two different scales."""
        repository = repo(
            [{"matter_ref": "M", "title": "", "version_id": "V-1",
              "shared": [{"hash": "a", "df": 1}]}],
            [],
        )

        repository.find_matches("acme", ["a"])

        weights = repository.graph.statements[1]
        assert "RETURN DISTINCT other" in weights
        assert "WITH DISTINCT v, c" in weights

    def test_boilerplate_is_weighted_down(self):
        """A clause in fifty versions is not evidence of anything."""
        common = [{"hash": h, "df": 50} for h in ("a", "b", "c", "d")]
        repository = repo(
            [{"matter_ref": "MSA-2026-0042", "title": "Acme", "version_id": "V-1",
              "shared": common}],
            [{"version_id": "V-1", "dfs": [50, 50, 50, 50] + [1] * 12}],
        )

        assert repository.find_matches("acme", ["a", "b", "c", "d"]) == []


class TestOneBadChunkDoesNotCostTheOthers:
    """`embed_documents` raises on the first failure, so a batch was
    all-or-nothing: one poison chunk in two hundred discarded 199 good vectors,
    and nothing revisits a chunk that exists without one."""

    def test_a_batch_failure_falls_back_to_one_at_a_time(self):
        document = identify_chunks(CONTRACT)
        poison = document.chunks[1].content
        calls = []

        def flaky(texts):
            calls.append(list(texts))
            if len(texts) > 1 or texts[0] == poison:
                raise RuntimeError("that one is too long")
            return [[0.5] * EMBEDDING_DIMENSIONS]

        repository = repo([], ATTACHED, embedder=flaky)

        result = repository.store_version_chunks("acme", "V-1", document)

        assert result["embedded"] == len(document.chunks) - 1
        assert result["failed"] == 1
        assert len(calls) == 1 + len(document.chunks), "no per-chunk retry happened"

    def test_a_chunk_left_without_a_vector_can_be_picked_up_later(self):
        repository = repo(
            [{"hash": "abc", "content": "some text"}],  # what is missing a vector
            [],                                         # the write
        )

        result = repository.backfill_embeddings("acme")

        assert result == {"found": 1, "embedded": 1}
        assert "c.embedding IS NULL" in repository.graph.statements[0]

    def test_the_backfill_also_catches_vectors_from_another_model(self):
        repository = repo([])

        repository.backfill_embeddings("acme")

        statement = repository.graph.statements[0]
        assert "c.embedding_model <> $model" in statement

    def test_nothing_to_backfill_is_not_an_embedding_call(self):
        embedder = CountingEmbedder()
        repository = repo([], embedder=embedder)

        assert repository.backfill_embeddings("acme") == {"found": 0, "embedded": 0}
        assert embedder.texts == []


class TestAVersionKeepsItsOwnWording:
    """`c.content` is written ON CREATE only, so a version that reuses a chunk
    shows whichever version created it. `canonical()` folds case, so
    "3. FEES AND PAYMENT" and "3. Fees and Payment" share a hash and differ on
    screen — and a defined term is exactly where that matters."""

    def test_the_relationship_carries_the_text_when_it_differs(self):
        repository = repo([], ATTACHED)

        repository.store_version_chunks("acme", "V-2", identify_chunks(CONTRACT))

        write = [s for s in repository.graph.statements if "MERGE (c:Chunk" in s][0]
        assert "i.text = CASE WHEN c.content = row.content THEN NULL ELSE row.content END" in write

    def test_case_only_differences_share_a_hash(self):
        """Which is why the per-version text is needed at all."""
        from backend.domain.chunking import chunk_hash

        assert chunk_hash("3. FEES AND PAYMENT. Net 30.") == chunk_hash(
            "3. Fees and Payment. Net 30.")


class TestAStoreThatWroteNothingSaysSo:
    def test_a_missing_version_is_reported_rather_than_counted(self):
        """The MATCH finds nothing, the UNWIND never runs, and the old code
        still returned "37 chunks stored"."""
        repository = repo([], [])

        result = repository.store_version_chunks("acme", "V-gone", identify_chunks(CONTRACT))

        assert result["stored"] == 0
        assert result["reused"] == 0
        assert result["failed"] == result["chunks"]

    def test_a_real_store_reports_what_it_attached(self):
        repository = repo([], [{"attached": 4}])

        result = repository.store_version_chunks("acme", "V-1", identify_chunks(CONTRACT))

        assert result["stored"] == 4


class TestRetentionNeverDeletesHistory:
    def test_only_chunks_no_version_references_are_removed(self):
        repository = repo([{"removed": 0}])

        repository.delete_orphan_chunks("acme")

        statement = repository.graph.statements[0]
        assert "NOT (:ContractVersion)-[:INCLUDES]->(c)" in statement

    def test_chunks_from_before_this_increment_are_out_of_reach(self):
        """They hang off a (:Document) and are referenced by no version, so
        "unreferenced" describes every one of them — 3,508 on the development
        database. Without this guard a routine tidy-up deletes the previous
        search corpus."""
        repository = repo([{"removed": 0}])

        repository.delete_orphan_chunks()

        statement = repository.graph.statements[0]
        assert "c.hash IS NOT NULL" in statement
        assert "c.tenant_id IS NOT NULL" in statement


class TestChunkSearchSeesBothCorpora:
    """Removing the old chunking path removed the only writer of
    `(:Document)-[:HAS_CHUNK]->(:Chunk)`, which is the only shape the chat
    agent's chunk search read. Without this, chunk search silently stops seeing
    every new upload — no error, no empty-result signal, the corpus just quietly
    stops growing.
    """

    @staticmethod
    def _source():
        import inspect

        from backend.shared.utils import enhanced_contract_search_tool

        return inspect.getsource(enhanced_contract_search_tool._search_chunks)

    def test_it_reads_content_addressed_chunks(self):
        assert "(v:ContractVersion {tenant_id: $tenant_id})-[:INCLUDES]->(c:Chunk)" in self._source()

    def test_it_still_reads_the_corpus_written_before_this_increment(self):
        """3,508 chunks that nothing else can reach."""
        assert "(d:Document)-[:HAS_CHUNK]->(c:Chunk)" in self._source()

    def test_the_semantic_query_orders_before_it_aggregates(self):
        """`ORDER BY chunk_score` after an aggregating RETURN is a SyntaxError,
        which the surrounding except swallowed — so this path had never once
        been semantic, silently falling back to substring matching."""
        source = self._source()

        semantic = source[source.index("semantic_query"): source.index("chunk_embedding = ")]
        # Exactly one, and it comes before the RETURN that aggregates.
        assert semantic.count("ORDER BY chunk_score") == 1
        assert semantic.index("ORDER BY chunk_score") < semantic.index("RETURN {")

    def test_both_branches_are_tenant_scoped(self):
        source = self._source()

        assert source.count("$tenant_id") >= 4
