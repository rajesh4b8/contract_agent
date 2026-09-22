"""Chunks as shared, content-addressed nodes.

    (:ContractVersion)-[:INCLUDES {order, heading}]->(:Chunk {tenant_id, hash, embedding})

Two things follow from that shape, and they are the increment:

**Unchanged text is never re-embedded.** Embedding is one network call per
chunk — ~210ms, so ~42s for a 200-chunk contract — and a new round of a contract
usually changes a handful of paragraphs. The `MERGE` does the work: if the chunk
node is already there, so is its embedding.

**Two versions can be compared at all.** "What moved since last round" is a set
difference over hashes. Unchanged chunks are *referenced* by the new version,
not copied into it.

The old storage did neither. It `MERGE`d a `(:Document)` on the **filename** and
then `CREATE`d fresh `(:Chunk)` nodes every time, so v1 and v2 chunks piled up
under one node with no way to tell which version a search hit came from, and
every upload paid the full embedding cost.

Nothing here deletes. A chunk a newer version replaced is still referenced by
the version that used it — that is the history this system exists to keep.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.domain.chunking import (
    ChunkingProfile,
    MatchCandidate,
    MATCH_THRESHOLD,
    rarity_weight,
)
from backend.domain.version_diff import cosine
from backend.infrastructure.chunking.identity import ChunkedDocument, IdentifiedChunk


@dataclass(frozen=True)
class MemberChunk:
    """One entry of a version's `INCLUDES` list, as the diff reads it."""

    hash: str
    order: int
    heading: str = ""
    text: str = ""
from backend.shared.debug import note, trace_step
from backend.shared.utils.contract_search_tool import graph as default_graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

#: Stored on every chunk beside its vector. A model change has to force a
#: re-embed rather than silently serving vectors from a different model in the
#: same similarity search — the pattern already used for Section and Clause.
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 1536


def _default_embedder(texts: Sequence[str]) -> List[List[float]]:
    from backend.shared.utils.gemini_embedding_service import GeminiEmbeddingService

    return GeminiEmbeddingService().embed_documents(list(texts))


class ChunkRepository:
    """Graph access for content-addressed chunks. Injectable for testing."""

    def __init__(self, graph: Any = None,
                 embedder: Optional[Callable[[Sequence[str]], List[List[float]]]] = None):
        self.graph = default_graph if graph is None else graph
        self._embedder = embedder or _default_embedder

    # -- reading ----------------------------------------------------------

    def existing_hashes(self, tenant_id: str, hashes: Sequence[str]) -> set:
        """Which of these chunks this tenant already holds *with a usable vector*.

        Guarded on the model and dimensions, not merely on the node existing: a
        chunk embedded by a previous model has a vector that cannot be compared
        with new ones, and silently reusing it is worse than paying to redo it.
        """
        if not hashes:
            return set()
        rows = self.graph.query(
            """
            MATCH (c:Chunk {tenant_id: $tenant_id})
            WHERE c.hash IN $hashes
              AND c.embedding IS NOT NULL
              AND c.embedding_model = $model
              AND c.embedding_dimensions = $dimensions
            RETURN c.hash AS hash
            """,
            {
                "tenant_id": tenant_id,
                "hashes": list(hashes),
                "model": EMBEDDING_MODEL,
                "dimensions": EMBEDDING_DIMENSIONS,
            },
        )
        return {row["hash"] for row in rows}

    # -- writing ----------------------------------------------------------

    def store_version_chunks(self, tenant_id: str, version_id: str,
                             document: ChunkedDocument) -> Dict[str, Any]:
        """Attach a version's chunks, embedding only what is genuinely new.

        Returns what it did, because "we skipped 194 of 200 embeddings" is the
        whole claim of this increment and it should be visible in the debug
        timeline rather than inferred from how long the upload took.
        """
        chunks = document.chunks
        if not chunks:
            return {"chunks": 0, "reused": 0, "embedded": 0, "failed": 0}

        wanted = [c.hash for c in chunks]
        with trace_step("chunking", "reuse_check", chunks=len(chunks)) as step:
            already = self.existing_hashes(tenant_id, wanted)
            step.set(reused=len(already), to_embed=len(set(wanted) - already))

        # One entry per *distinct* hash: a document that repeats a paragraph
        # should pay for it once.
        to_embed: Dict[str, IdentifiedChunk] = {}
        for chunk in chunks:
            if chunk.hash not in already and chunk.hash not in to_embed:
                to_embed[chunk.hash] = chunk

        vectors: Dict[str, List[float]] = {}
        if to_embed:
            with trace_step("chunking", "embed_new", chunks=len(to_embed)) as step:
                vectors = self._embed(to_embed)
                step.set(embedded=len(vectors), failed=len(to_embed) - len(vectors))

        rows = [
            {
                "hash": chunk.hash,
                "order": chunk.order,
                "heading": chunk.heading,
                "content": chunk.content,
                "chunk_type": chunk.chunk_type,
                "size": chunk.size,
                # Only the first occurrence of a hash carries a vector; the
                # MERGE below leaves an existing one alone.
                "embedding": vectors.get(chunk.hash) if chunk.hash in vectors else None,
            }
            for chunk in chunks
        ]

        # `c.content` is written ON CREATE only, so a version that *reuses* a
        # chunk shows the text of whichever version created it. Usually
        # identical — but `canonical()` folds case and whitespace, so
        # "3. FEES AND PAYMENT" and "3. Fees and Payment" share a hash while
        # differing on screen, and a defined term is exactly where that matters.
        # The version's own rendering rides on the relationship when it differs,
        # which costs a few KB against the 6KB vector it is saving.
        for row in rows:
            row["text"] = None

        with trace_step("chunking", "store", chunks=len(rows)) as step:
            attached = self.graph.query(
                """
                MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
                // Replace this version's membership list, not the chunks. The
                // chunks are shared; deleting one because a newer version
                // stopped referencing it would delete another version's text.
                OPTIONAL MATCH (v)-[old:INCLUDES]->(:Chunk)
                DELETE old
                WITH DISTINCT v
                UNWIND $rows AS row
                // Keyed on (tenant_id, hash) and never on hash alone: a global
                // key would collapse two customers' identical boilerplate onto
                // one node, which is a tenancy violation and makes a GDPR
                // deletion destroy someone else's version.
                MERGE (c:Chunk {tenant_id: $tenant_id, hash: row.hash})
                ON CREATE SET c.content = row.content,
                              c.chunk_type = row.chunk_type,
                              c.size = row.size,
                              c.created_at = datetime()
                // The vector is written only when one was produced, so a reused
                // chunk keeps the embedding it already had.
                FOREACH (_ IN CASE WHEN row.embedding IS NOT NULL THEN [1] ELSE [] END |
                    SET c.embedding = row.embedding,
                        c.embedding_model = $model,
                        c.embedding_dimensions = $dimensions,
                        c.embedding_generated_at = datetime()
                )
                MERGE (v)-[i:INCLUDES {order: row.order}]->(c)
                SET i.heading = row.heading,
                    // Only when this version renders the chunk differently from
                    // the node's stored copy; null the rest of the time.
                    i.text = CASE WHEN c.content = row.content THEN NULL ELSE row.content END
                RETURN count(i) AS attached
                """,
                {
                    "version_id": version_id,
                    "tenant_id": tenant_id,
                    "rows": rows,
                    "model": EMBEDDING_MODEL,
                    "dimensions": EMBEDDING_DIMENSIONS,
                },
            )
            # An empty result means the MATCH found no such version, in which
            # case the UNWIND never ran and nothing was written. Reporting
            # "37 chunks stored" for a no-op would put a number in the debug
            # panel and the API response that is simply untrue.
            stored = int(attached[0]["attached"]) if attached else 0
            step.set(stored=stored)

        if not stored:
            logger.error(
                f"No chunks attached to {version_id}: no such version for tenant "
                f"{tenant_id}. The chunks were not stored."
            )
            note("chunking", "store", "error", version_id=version_id,
                 reason="version not found")
            return {"chunks": len(chunks), "distinct": len(set(wanted)),
                    "reused": 0, "embedded": 0, "failed": len(chunks),
                    "stored": 0}

        result = {
            "chunks": len(chunks),
            "distinct": len(set(wanted)),
            "reused": len(already),
            "embedded": len(vectors),
            "failed": len(to_embed) - len(vectors),
            "stored": stored,
        }
        note("chunking", "stored", version_id=version_id, **result)
        logger.info(
            f"Version {version_id}: {result['chunks']} chunks, "
            f"{result['reused']} reused, {result['embedded']} embedded"
        )
        return result

    def _embed(self, to_embed: Dict[str, IdentifiedChunk]) -> Dict[str, List[float]]:
        """Embed the new chunks, keeping whatever succeeds.

        `embed_documents` raises on the first failure, so one bad chunk in two
        hundred used to discard all 199 good vectors — and nothing revisits a
        chunk that exists without one, so they would have stayed unembedded for
        ever. The batch is tried first because it is one round trip; only if it
        raises does this fall back to per-chunk, so a single poison chunk costs
        its neighbours nothing.
        """
        hashes = list(to_embed)
        try:
            produced = self._embedder([to_embed[h].content for h in hashes])
            return dict(zip(hashes, produced))
        except Exception as e:
            logger.warning(
                f"Batch embedding of {len(hashes)} chunks failed ({e}); "
                f"retrying them one at a time"
            )

        vectors: Dict[str, List[float]] = {}
        failed = 0
        for digest in hashes:
            try:
                vectors[digest] = self._embedder([to_embed[digest].content])[0]
            except Exception as e:
                failed += 1
                logger.error(f"Embedding chunk {digest[:12]} failed: {e}")
        if failed:
            note("chunking", "embed_new", "error", failed=failed, embedded=len(vectors))
        return vectors

    def backfill_embeddings(self, tenant_id: str, limit: int = 200) -> Dict[str, int]:
        """Embed chunks that were stored without a vector.

        The other half of the fix above. A chunk whose embedding failed is
        invisible to `existing_hashes` — correctly, since it has no usable
        vector — but that also means nothing would ever retry it, and it would
        sit unsearchable until somebody happened to re-upload the same text.
        """
        rows = self.graph.query(
            """
            MATCH (c:Chunk {tenant_id: $tenant_id})
            WHERE c.hash IS NOT NULL
              AND (c.embedding IS NULL
                   OR c.embedding_model <> $model
                   OR c.embedding_dimensions <> $dimensions)
            RETURN c.hash AS hash, c.content AS content
            LIMIT $limit
            """,
            {"tenant_id": tenant_id, "model": EMBEDDING_MODEL,
             "dimensions": EMBEDDING_DIMENSIONS, "limit": limit},
        )
        if not rows:
            return {"found": 0, "embedded": 0}

        pending = {
            row["hash"]: IdentifiedChunk(order=0, hash=row["hash"],
                                         content=row.get("content") or "")
            for row in rows if (row.get("content") or "").strip()
        }
        vectors = self._embed(pending)
        for digest, vector in vectors.items():
            self.graph.query(
                """
                MATCH (c:Chunk {tenant_id: $tenant_id, hash: $hash})
                SET c.embedding = $embedding,
                    c.embedding_model = $model,
                    c.embedding_dimensions = $dimensions,
                    c.embedding_generated_at = datetime()
                """,
                {"tenant_id": tenant_id, "hash": digest, "embedding": vector,
                 "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIMENSIONS},
            )
        logger.info(f"Backfilled {len(vectors)} of {len(rows)} missing embeddings")
        return {"found": len(rows), "embedded": len(vectors)}

    def set_profile(self, tenant_id: str, version_id: str, profile: ChunkingProfile,
                    boundaries_are_structural: bool = True) -> None:
        """Record how this version was chunked, so later rounds match it."""
        self.graph.query(
            """
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            SET v.chunking_profile = $profile,
                v.chunk_boundaries_structural = $structural
            """,
            {
                "version_id": version_id,
                "tenant_id": tenant_id,
                # Flattened rather than nested: Neo4j properties cannot hold maps.
                "profile": _flatten(profile),
                "structural": boundaries_are_structural,
            },
        )

    def profile_for_version(self, tenant_id: str, version_id: str) -> Optional[ChunkingProfile]:
        """The profile this exact version was chunked with.

        Analysis reads this rather than chunking afresh with a default: a matter
        whose first round was chunked with different sizes, or by a different
        extractor, has stored chunks a default profile cannot reproduce.
        """
        rows = self.graph.query(
            """
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            WHERE v.chunking_profile IS NOT NULL
            RETURN v.chunking_profile AS profile
            """,
            {"version_id": version_id, "tenant_id": tenant_id},
        )
        if not rows or not rows[0].get("profile"):
            return None
        return ChunkingProfile.from_dict(_unflatten(rows[0]["profile"]))

    def profile_for_matter(self, tenant_id: str, matter_ref: str) -> Optional[ChunkingProfile]:
        """The profile version 1 was chunked with, if this matter has one.

        Strategy selection runs once, for the first version. Re-running
        threshold-based scoring on every round means a one-word edit can flip a
        document from `section` to `paragraph` chunking, and then no chunk
        survives — for no reason a reader could ever see.
        """
        rows = self.graph.query(
            """
            MATCH (m:Matter {matter_ref: $matter_ref, tenant_id: $tenant_id})
                  -[r:HAS_VERSION]->(v:ContractVersion)
            WHERE v.chunking_profile IS NOT NULL
            RETURN v.chunking_profile AS profile
            ORDER BY r.n
            LIMIT 1
            """,
            {"matter_ref": matter_ref, "tenant_id": tenant_id},
        )
        if not rows or not rows[0].get("profile"):
            return None
        return ChunkingProfile.from_dict(_unflatten(rows[0]["profile"]))

    # -- advisory matching ------------------------------------------------

    def find_matches(self, tenant_id: str, hashes: Sequence[str],
                     exclude_version: Optional[str] = None,
                     limit: int = 5) -> List[MatchCandidate]:
        """Matters that share text with an incoming document.

        A lookup of the incoming document's own hashes, not a scan: the cost is
        O(chunks in the new document), not O(chunks in the tenant).

        Shared chunks are weighted by `1/log(1+df)`, where `df` is how many
        versions hold that chunk. Unweighted, a tenant's standard boilerplate
        would make every contract look like a new round of every other.

        **It decides nothing.** The result sits beside the choice the user was
        going to make anyway — a new SOW for a different vendor off the same
        template is indistinguishable from a new round, and only they know.
        """
        distinct = list(dict.fromkeys(h for h in hashes if h))
        if not distinct:
            return []

        rows = self.graph.query(
            """
            UNWIND $hashes AS h
            MATCH (c:Chunk {tenant_id: $tenant_id, hash: h})
            // Document frequency counts distinct **matters**, not versions.
            //
            // Counting versions gets this exactly backwards: a clause carried
            // through four rounds of one negotiation is the strongest evidence
            // a match could have, and version-counting scores it as boilerplate
            // — so the more rounds a matter has, the less it looks like itself.
            // Observed: a lookalike sharing 12 of 14 chunks with a three-round
            // matter scored 79% and fell below the threshold, while the same
            // document against a one-round matter scored 85%.
            //
            // Boilerplate is text that turns up across *different* contracts.
            WITH c, h, COUNT {
                MATCH (c)<-[:INCLUDES]-(:ContractVersion)<-[:HAS_VERSION]-(other:Matter)
                RETURN DISTINCT other
            } AS df
            MATCH (c)<-[:INCLUDES]-(v:ContractVersion)<-[:HAS_VERSION]-(m:Matter)
            WHERE m.status <> 'CLOSED'
              AND ($exclude IS NULL OR v.version_id <> $exclude)
            WITH m, v, h, df
            RETURN m.matter_ref AS matter_ref,
                   m.title AS title,
                   v.version_id AS version_id,
                   collect(DISTINCT {hash: h, df: df}) AS shared
            """,
            {"tenant_id": tenant_id, "hashes": distinct, "exclude": exclude_version},
        )
        if not rows:
            return []

        totals = self._version_weights(tenant_id, [r["version_id"] for r in rows])
        # The incoming document's own weight, using the same rarity weighting,
        # so forward and backward are measured on one scale.
        incoming_df = {}
        for row in rows:
            for item in row["shared"]:
                incoming_df[item["hash"]] = max(incoming_df.get(item["hash"], 1), item["df"])
        new_total = sum(rarity_weight(incoming_df.get(h, 1)) for h in distinct)

        best: Dict[str, MatchCandidate] = {}
        for row in rows:
            shared = sum(rarity_weight(item["df"]) for item in row["shared"])
            candidate = MatchCandidate(
                matter_ref=row["matter_ref"],
                title=row.get("title") or "",
                version=None,
                shared=shared,
                new_total=new_total,
                candidate_total=totals.get(row["version_id"], shared),
            )
            existing = best.get(candidate.matter_ref)
            if existing is None or candidate.score > existing.score:
                best[candidate.matter_ref] = candidate

        ranked = sorted(best.values(), key=lambda c: c.score, reverse=True)
        return [c for c in ranked if c.score >= MATCH_THRESHOLD][:limit]

    def _version_weights(self, tenant_id: str, version_ids: Sequence[str]) -> Dict[str, float]:
        """Each candidate version's total weight, for the backward direction."""
        ids = [v for v in dict.fromkeys(version_ids) if v]
        if not ids:
            return {}
        rows = self.graph.query(
            """
            MATCH (v:ContractVersion {tenant_id: $tenant_id})-[:INCLUDES]->(c:Chunk)
            WHERE v.version_id IN $ids
            // DISTINCT, because `shared` above is built from distinct hashes. A
            // version that includes the same chunk twice — a repeated
            // "Reserved." subsection, a page header extracted twice — would
            // otherwise be counted twice here and once there, understating
            // `backward` and dropping a real new round below the threshold.
            WITH DISTINCT v, c
            // The same definition as above, or forward and backward would be
            // measured on two different scales.
            WITH v, c, COUNT {
                MATCH (c)<-[:INCLUDES]-(:ContractVersion)<-[:HAS_VERSION]-(other:Matter)
                RETURN DISTINCT other
            } AS df
            RETURN v.version_id AS version_id, collect(df) AS dfs
            """,
            {"tenant_id": tenant_id, "ids": ids},
        )
        return {
            row["version_id"]: sum(rarity_weight(df) for df in row["dfs"])
            for row in rows
        }

    # -- version comparison -----------------------------------------------

    def version_membership(self, tenant_id: str, version_id: str) -> List[Any]:
        """A version's chunks in reading order, as the diff needs them.

        The `INCLUDES` list itself — the same structure Increment 7 stores and
        Increment 8 windows over — rather than a re-derivation of it. Comparing
        two rounds is only meaningful over what was actually recorded.
        """
        rows = self.graph.query(
            """
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
                  -[i:INCLUDES]->(c:Chunk)
            RETURN c.hash AS hash, i.order AS order, i.heading AS heading,
                   coalesce(i.text, c.content) AS text
            ORDER BY i.order
            """,
            {"version_id": version_id, "tenant_id": tenant_id},
        )
        return [
            MemberChunk(
                hash=row["hash"],
                order=int(row["order"] or 0),
                heading=row.get("heading") or "",
                text=row.get("text") or "",
            )
            for row in rows if row.get("hash")
        ]

    def similarity_lookup(self, tenant_id: str, hashes: Sequence[str]) -> Callable:
        """A `similarity(old_hash, new_hash)` over the stored embeddings.

        Fetched once, up front, rather than a query per comparison: a `replace`
        block on a long contract can hold dozens of pairs, and one round trip
        each would make the diff slower than the analysis it exists to avoid.

        Returns None for any chunk without a usable vector, which the diff reads
        as "not measured" and reports as a removal plus an addition — showing
        the reviewer both texts rather than claiming a link nothing checked.
        """
        wanted = [h for h in dict.fromkeys(hashes) if h]
        vectors: Dict[str, List[float]] = {}
        if wanted:
            rows = self.graph.query(
                """
                MATCH (c:Chunk {tenant_id: $tenant_id})
                WHERE c.hash IN $hashes
                  AND c.embedding IS NOT NULL
                  AND c.embedding_model = $model
                  AND c.embedding_dimensions = $dimensions
                RETURN c.hash AS hash, c.embedding AS embedding
                """,
                {
                    "tenant_id": tenant_id, "hashes": wanted,
                    "model": EMBEDDING_MODEL, "dimensions": EMBEDDING_DIMENSIONS,
                },
            )
            vectors = {row["hash"]: row["embedding"] for row in rows if row.get("embedding")}

        def similarity(old_hash: str, new_hash: str) -> Optional[float]:
            return cosine(vectors.get(old_hash), vectors.get(new_hash))

        return similarity

    # -- retention --------------------------------------------------------

    def delete_orphan_chunks(self, tenant_id: Optional[str] = None) -> int:
        """Remove chunks no version references at all.

        The **only** justification for deleting a chunk. A superseded one is
        still referenced by the version that used it, and that is not garbage —
        it is the negotiation history. True orphans come from failed uploads and
        withdrawn versions.

        **Scoped to content-addressed chunks.** Chunks written before this
        increment hang off a `(:Document)` by `HAS_CHUNK` and are referenced by
        no version at all, so "unreferenced by a version" describes every one of
        them — 3,508 on the development database. Without the `c.hash IS NOT
        NULL` guard, a routine tidy-up would silently delete the entire previous
        search corpus. Retiring those is a deliberate act, not a side effect of
        garbage collection.
        """
        rows = self.graph.query(
            """
            MATCH (c:Chunk)
            WHERE ($tenant_id IS NULL OR c.tenant_id = $tenant_id)
              AND c.hash IS NOT NULL
              AND c.tenant_id IS NOT NULL
              AND NOT (:ContractVersion)-[:INCLUDES]->(c)
            WITH collect(c) AS orphans
            FOREACH (chunk IN orphans | DETACH DELETE chunk)
            RETURN size(orphans) AS removed
            """,
            {"tenant_id": tenant_id},
        )
        return int(rows[0]["removed"]) if rows else 0


def _flatten(profile: ChunkingProfile) -> List[str]:
    """A profile as `key=value` strings — Neo4j properties cannot hold maps."""
    return [f"{key}={value}" for key, value in sorted(profile.to_dict().items())]


def _unflatten(stored: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for entry in stored or []:
        key, _, value = str(entry).partition("=")
        if not key:
            continue
        out[key] = int(value) if value.lstrip("-").isdigit() else value
    return out
