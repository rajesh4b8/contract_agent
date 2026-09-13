"""Reading and writing matters, versions and reference numbers.

The shape, and the one decision worth calling out: **a version is the existing
``(:Contract)`` node, given a second label.**

    (:Matter)-[:HAS_VERSION {n}]->(:Contract:ContractVersion)

Not a new node beside it. Every redline a reviewer decided in Increment 4 hangs
off ``(:Contract)-[:HAS_REDLINE]->(:Redline)`` and is looked up by
``file_id``; every URL in the app carries that same id. Introducing a separate
node would mean re-pointing all of it and migrating the decisions across — the
one thing this increment must not risk. Adding a label re-points nothing, and
``version_id == file_id`` keeps the existing analyse/decide flow working
untouched.

Nothing here deletes. The migration adds labels and Matter nodes to contracts
that predate them, and is safe to re-run.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.domain.matter import (
    AnalysisStatus,
    MatterStatus,
    counterparty_from_parties,
    format_reference,
    parse_status,
    source_sha256,
    suggest_title,
    type_code,
)
from backend.shared.utils.contract_search_tool import graph as default_graph
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)


def _is_constraint_violation(error: Exception) -> bool:
    """Whether a driver error is a uniqueness-constraint rejection.

    Matched on the message because the Neo4j driver raises
    `ClientError` for a whole family of problems and importing its
    exception hierarchy here would drag the driver into modules that
    currently need nothing but a `query()` callable — including the tests.
    """
    text = f"{type(error).__name__}: {error}".lower()
    return "constraint" in text and ("already exists" in text or "equivalent" in text)


class MatterNotFound(LookupError):
    """No such matter for this tenant.

    Deliberately not distinguishable from "belongs to someone else": the
    endpoint answers 404 either way, the same rule redlines follow. Answering
    403 for another tenant's reference would confirm that it exists.
    """


class MatterClosed(RuntimeError):
    """A closed matter takes no new rounds. Reopening is an explicit action."""


class AlreadyFiled(RuntimeError):
    """This version is already part of a matter."""


#: The constraints that make the concurrency guarantees real rather than
#: hopeful. `MERGE` alone does not make a node singleton: two transactions can
#: both find nothing and both create, which for `(:Counter)` means two counters
#: that each return 1 and two matters that claim the same reference. Only a
#: uniqueness constraint serialises that.
#:
#: `source_key` is `tenant_id|source_sha256`, set **only on versions uploaded
#: after this increment**. Neo4j uniqueness constraints ignore nodes where the
#: property is null, so contracts migrated from before matters existed are
#: exempt — which they have to be, because re-uploading the same PDF is exactly
#: the bug this increment fixes and the history is full of it.
CONSTRAINTS = (
    ("matter_counter_unique",
     "FOR (c:Counter) REQUIRE (c.tenant_id, c.year, c.kind) IS UNIQUE"),
    ("matter_ref_unique",
     "FOR (m:Matter) REQUIRE (m.tenant_id, m.matter_ref) IS UNIQUE"),
    ("contract_source_key_unique",
     "FOR (v:ContractVersion) REQUIRE v.source_key IS UNIQUE"),
)


def source_key(tenant_id: str, digest: str) -> str:
    """The value the uniqueness constraint above is enforced on."""
    return f"{tenant_id}|{digest}"


class DuplicateSource(RuntimeError):
    """These exact bytes already exist as a version for this tenant.

    Raised by the write rather than discovered by a lookup, so it is a real
    answer under concurrency instead of a hopeful one.
    """

    def __init__(self, message: str = "", existing: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.existing = existing


class MatterRepository:
    """Graph access for matters. The graph is injectable so tests need no server."""

    def __init__(self, graph: Any = None):
        self.graph = default_graph if graph is None else graph

    def ensure_constraints(self) -> List[str]:
        """Create the uniqueness constraints, idempotently.

        Called at startup and by the migration script. Failures are reported
        rather than raised: an existing database may hold rows that violate a
        constraint, and refusing to start is a worse outcome than running
        without it and saying so.
        """
        created: List[str] = []
        for name, body in CONSTRAINTS:
            try:
                self.graph.query(f"CREATE CONSTRAINT {name} IF NOT EXISTS {body}")
                created.append(name)
            except Exception as e:
                logger.warning(f"Could not create constraint {name}: {e}")
        return created

    # -- versions ---------------------------------------------------------

    def record_version(self, tenant_id: str, version_id: str, *,
                       source_sha256: str, filename: str = "",
                       claim_source: bool = True) -> Optional[Dict[str, Any]]:
        """Mark a freshly stored contract as a version, before it is filed.

        A version exists from the moment extraction succeeds, whether or not the
        user has confirmed which matter it belongs to. That is what makes the
        confirmation card cancellable without leaving a reference number burned.

        `claim_source` writes `source_key`, which carries a uniqueness
        constraint. This is where the duplicate guarantee is actually enforced:
        the pre-flight hash lookup at the start of an upload is a fast path that
        saves two minutes of work, but between it and this write lies the whole
        extraction, so two uploads of the same PDF can both pass it. The
        constraint cannot be raced, and a violation here is reported as
        `DuplicateSource` naming the version that won.

        `claim_source=False` for the migration, where the same document legitimately
        appears many times — re-uploading a revised contract *was* the bug, so the
        history is full of it, and those versions stay exempt from the constraint.
        """
        try:
            rows = self.graph.query(
                """
                MATCH (c:Contract {file_id: $version_id, tenant_id: $tenant_id})
                SET c:ContractVersion,
                    c.version_id = $version_id,
                    c.source_sha256 = $source_sha256,
                    c.source_filename = $filename,
                    c.uploaded_at = coalesce(c.uploaded_at, c.upload_date, datetime()),
                    c.analysis_status = coalesce(c.analysis_status, $not_started)
                """
                + ("""
                SET c.source_key = $source_key
                """ if claim_source else "")
                + """
                RETURN c.version_id AS version_id
                """,
                {
                    "version_id": version_id,
                    "tenant_id": tenant_id,
                    "source_sha256": source_sha256,
                    "source_key": source_key(tenant_id, source_sha256),
                    "filename": filename,
                    "not_started": AnalysisStatus.NOT_STARTED.value,
                },
            )
        except Exception as e:
            if not _is_constraint_violation(e):
                raise
            # Another upload of the same bytes got there first. Report the one
            # that won, so the caller can send the reviewer to its matter.
            existing = self.version_by_source_hash(tenant_id, source_sha256)
            raise DuplicateSource(
                f"{version_id} duplicates an existing version for {tenant_id}",
                existing=existing,
            ) from e
        return rows[0] if rows else None

    def mark_superseded(self, tenant_id: str, version_id: str, winner_id: str) -> None:
        """Record that this contract lost a race to identical bytes.

        The loser of a duplicate race is a `(:Contract)` that never became a
        version: it holds no findings and no decisions, and nobody can reach it.
        Left as it is, the migration would later pick it up as an unlabelled
        legacy contract and give it a matter of its own — resurrecting the exact
        duplicate the constraint just prevented.

        Marked rather than deleted. Deleting is irreversible and this node is
        evidence that two uploads raced; the migration skips it instead.
        """
        try:
            self.graph.query(
                """
                MATCH (c:Contract {file_id: $version_id, tenant_id: $tenant_id})
                SET c.superseded_by = $winner_id, c.superseded_at = datetime()
                """,
                {"version_id": version_id, "tenant_id": tenant_id, "winner_id": winner_id},
            )
        except Exception as e:
            logger.warning(f"Could not mark {version_id} superseded: {e}")

    def version_by_source_hash(self, tenant_id: str, digest: str) -> Optional[Dict[str, Any]]:
        """The one automatic case: these exact bytes have been seen before.

        Tenant-scoped, unlike the filename check it replaces — that one matched
        ``c.file_id CONTAINS $filename`` across every tenant in the database and
        would hand a stranger's contract id back to the uploader. (It also never
        fired, because ``file_id`` is ``UPLOADED_{random}_{date}`` and never
        contains the filename.)

        A version already filed under a matter wins over a loose one, so a
        double-click on the confirmation card reports the matter rather than the
        orphan.
        """
        rows = self.graph.query(
            """
            MATCH (v:ContractVersion {tenant_id: $tenant_id, source_sha256: $digest})
            OPTIONAL MATCH (m:Matter {tenant_id: $tenant_id})-[r:HAS_VERSION]->(v)
            RETURN v.version_id AS version_id,
                   v.source_filename AS filename,
                   m.matter_ref AS matter_ref,
                   m.title AS title,
                   m.status AS matter_status,
                   r.n AS n
            ORDER BY CASE WHEN m IS NULL THEN 1 ELSE 0 END, v.uploaded_at
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "digest": digest},
        )
        return rows[0] if rows else None

    def set_analysis_status(self, tenant_id: str, version_id: str,
                            status: AnalysisStatus, error: str = "") -> None:
        """Record how the analysis of one version went.

        Called around the analysis rather than after it, so a matter opened
        while its analysis is still running shows the in-progress state instead
        of an empty review, and one that died shows the failure instead of
        "no findings".
        """
        try:
            self.graph.query(
                """
                MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
                SET v.analysis_status = $status,
                    v.analysis_error = $error,
                    v.analysis_updated_at = datetime()
                """,
                {
                    "version_id": version_id,
                    "tenant_id": tenant_id,
                    "status": status.value,
                    "error": error or "",
                },
            )
        except Exception as e:  # never fail an analysis over its own bookkeeping
            logger.warning(f"Could not set analysis status on {version_id}: {e}")

    # -- matters ----------------------------------------------------------

    def allocate_reference(self, tenant_id: str, kind: str, year: int) -> str:
        """Take the next number for this tenant, year and type.

        Read and increment happen in one statement, so two uploads racing for a
        reference cannot both read the same value: the second waits on the lock
        the first holds over the counter node.
        """
        rows = self.graph.query(
            """
            MERGE (c:Counter {tenant_id: $tenant_id, year: $year, kind: $kind})
              ON CREATE SET c.n = 0
            SET c.n = c.n + 1
            RETURN c.n AS n
            """,
            {"tenant_id": tenant_id, "kind": kind, "year": year},
        )
        if not rows:
            raise RuntimeError(f"could not allocate a {kind} reference for {tenant_id}")
        return format_reference(kind, year, int(rows[0]["n"]))

    def create_matter(self, tenant_id: str, version_id: str, *, title: str,
                      counterparty: str = "", contract_type: str = "",
                      year: Optional[int] = None) -> Dict[str, Any]:
        """File a version as version 1 of a brand-new matter.

        The reference is allocated here, **after** extraction has already
        succeeded, so a document that never parsed burns no number and leaves no
        gap in the sequence. The existence check comes before the allocation for
        the same reason: filing something that is not there must cost nothing.
        """
        unfiled = self.graph.query(
            """
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            WHERE NOT (:Matter)-[:HAS_VERSION]->(v)
            RETURN v.version_id AS version_id
            """,
            {"version_id": version_id, "tenant_id": tenant_id},
        )
        if not unfiled:
            raise AlreadyFiled(
                f"{version_id} is not an unfiled version of tenant {tenant_id}"
            )

        kind = type_code(contract_type)
        matter_ref = self.allocate_reference(tenant_id, kind, year or datetime.now().year)

        rows = self.graph.query(
            """
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            // Take the write lock on the version *before* asking whether it is
            // already filed. Without this the check above and this one are both
            // reads: two confirmations of the same document could each see it
            // unfiled, and each create a matter pointing at it — two references
            // for one contract, and no constraint to stop it, since nothing
            // limits a version to one incoming HAS_VERSION.
            //
            // A transaction that blocks here resumes *after* the winner
            // commits, and the predicate below is evaluated at that point, so
            // it sees the relationship and yields no rows.
            SET v.filing_attempts = coalesce(v.filing_attempts, 0) + 1
            WITH v
            WHERE NOT (:Matter)-[:HAS_VERSION]->(v)
            CREATE (m:Matter {
                matter_ref: $matter_ref,
                tenant_id: $tenant_id,
                title: $title,
                counterparty: $counterparty,
                contract_type: $contract_type,
                status: $status,
                version_count: 1,
                created_at: datetime(),
                updated_at: datetime()
            })
            CREATE (m)-[:HAS_VERSION {n: 1}]->(v)
            RETURN m.matter_ref AS matter_ref
            """,
            {
                "version_id": version_id,
                "tenant_id": tenant_id,
                "matter_ref": matter_ref,
                "title": title,
                "counterparty": counterparty or "",
                "contract_type": contract_type or "",
                "status": MatterStatus.DRAFT.value,
            },
        )
        if not rows:
            # Lost the race against a concurrent confirmation of the same
            # document. The reference just allocated is spent; a gap is a far
            # better outcome than two matters holding the same bytes.
            raise AlreadyFiled(
                f"{version_id} was filed by someone else while {matter_ref} was being created"
            )

        logger.info(f"Created matter {matter_ref} from version {version_id}")
        return {"matter_ref": matter_ref, "n": 1, "version_id": version_id}

    def attach_version(self, tenant_id: str, matter_ref: str, version_id: str) -> Dict[str, Any]:
        """Add a new round to an existing matter.

        ``n`` is derived and written inside the same statement that increments
        the matter's counter, so two uploads racing into one matter get 2 and 3
        rather than both getting 2.

        A matter awaiting the counterparty comes back to DRAFT when their
        version lands — which is what a new round *means* — while a closed one is
        refused above this call.
        """
        matter = self.get_matter(tenant_id, matter_ref)
        if matter is None:
            raise MatterNotFound(matter_ref)
        if not parse_status(matter["status"]).accepts_new_versions:
            raise MatterClosed(matter_ref)

        rows = self.graph.query(
            """
            MATCH (m:Matter {matter_ref: $matter_ref, tenant_id: $tenant_id})
            WHERE m.status <> $closed
            MATCH (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            // The version's lock first, so the ownership check below is made
            // under it — same reason as `create_matter`. A version belongs to
            // exactly one matter, and nothing in the schema enforces that.
            SET v.filing_attempts = coalesce(v.filing_attempts, 0) + 1
            WITH m, v
            WHERE NOT (:Matter)-[:HAS_VERSION]->(v)
            // Only now the matter's counter, so a refused attach leaves no gap
            // in the version numbers. Concurrent rounds serialise on this SET
            // and get 2 and 3 rather than both getting 2.
            SET m.version_count = coalesce(m.version_count, 0) + 1,
                m.updated_at = datetime(),
                m.status = CASE WHEN m.status = $awaiting THEN $draft ELSE m.status END
            CREATE (m)-[r:HAS_VERSION {n: m.version_count}]->(v)
            RETURN m.matter_ref AS matter_ref, r.n AS n
            """,
            {
                "matter_ref": matter_ref,
                "tenant_id": tenant_id,
                "version_id": version_id,
                "closed": MatterStatus.CLOSED.value,
                "awaiting": MatterStatus.AWAITING_COUNTERPARTY.value,
                "draft": MatterStatus.DRAFT.value,
            },
        )
        if not rows:
            raise MatterNotFound(f"{matter_ref}/{version_id}")

        return {"matter_ref": matter_ref, "n": int(rows[0]["n"]), "version_id": version_id}

    def get_matter(self, tenant_id: str, matter_ref: str) -> Optional[Dict[str, Any]]:
        """One matter and every round of it, or None.

        None for an unknown reference *and* for another tenant's — the endpoint
        answers 404 to both.
        """
        rows = self.graph.query(
            """
            MATCH (m:Matter {matter_ref: $matter_ref, tenant_id: $tenant_id})
            OPTIONAL MATCH (m)-[r:HAS_VERSION]->(v:ContractVersion)
            WITH m, r, v ORDER BY r.n
            RETURN m.matter_ref AS matter_ref,
                   m.title AS title,
                   m.counterparty AS counterparty,
                   m.contract_type AS contract_type,
                   m.status AS status,
                   toString(m.created_at) AS created_at,
                   toString(m.updated_at) AS updated_at,
                   [x IN collect({
                       n: r.n,
                       version_id: v.version_id,
                       filename: v.source_filename,
                       source_sha256: v.source_sha256,
                       uploaded_at: toString(v.uploaded_at),
                       analysis_status: coalesce(v.analysis_status, $not_started),
                       analysis_error: v.analysis_error,
                       analysis_updated_at: toString(v.analysis_updated_at),
                       risk_score: v.risk_score,
                       risk_level: v.risk_level,
                       clauses_count: v.clauses_count,
                       violations_count: v.violations_count,
                       redlines_count: v.redlines_count
                   }) WHERE x.version_id IS NOT NULL] AS versions
            """,
            {
                "matter_ref": matter_ref,
                "tenant_id": tenant_id,
                "not_started": AnalysisStatus.NOT_STARTED.value,
            },
        )
        return rows[0] if rows else None

    def count_matters(self, tenant_id: str) -> int:
        """How many matters this tenant has, so a truncated list can say so."""
        rows = self.graph.query(
            "MATCH (m:Matter {tenant_id: $tenant_id}) RETURN count(m) AS total",
            {"tenant_id": tenant_id},
        )
        return int(rows[0]["total"]) if rows else 0

    def list_matters(self, tenant_id: str, limit: int = 200,
                     offset: int = 0) -> List[Dict[str, Any]]:
        """Every matter for this tenant, newest activity first.

        Carries the latest version's headline numbers and its pending-redline
        count, because the list is the landing page: it has to say what state
        each review is in without a request per row.
        """
        return self.graph.query(
            """
            MATCH (m:Matter {tenant_id: $tenant_id})
            OPTIONAL MATCH (m)-[r:HAS_VERSION]->(:ContractVersion)
            WITH m, count(r) AS version_count, max(r.n) AS latest_n
            OPTIONAL MATCH (m)-[:HAS_VERSION {n: latest_n}]->(latest:ContractVersion)
            OPTIONAL MATCH (latest)-[:HAS_REDLINE]->(rl:Redline)
            WITH m, version_count, latest_n, latest,
                 count(rl) AS redline_total,
                 // `rl IS NOT NULL` is load-bearing: the OPTIONAL MATCH yields a
                 // null row for a version with no redlines, and
                 // `coalesce(null.status, 'PENDING')` is 'PENDING' — so a matter
                 // that has never been analysed reported one pending redline.
                 sum(CASE WHEN rl IS NOT NULL AND coalesce(rl.status, 'PENDING') = 'PENDING'
                          THEN 1 ELSE 0 END) AS redline_pending
            RETURN m.matter_ref AS matter_ref,
                   m.title AS title,
                   m.counterparty AS counterparty,
                   m.contract_type AS contract_type,
                   m.status AS status,
                   toString(m.created_at) AS created_at,
                   toString(m.updated_at) AS updated_at,
                   version_count,
                   latest_n,
                   latest.version_id AS latest_version_id,
                   coalesce(latest.analysis_status, $not_started) AS analysis_status,
                   latest.risk_score AS risk_score,
                   latest.risk_level AS risk_level,
                   redline_total,
                   redline_pending
            ORDER BY m.updated_at DESC, m.matter_ref DESC
            SKIP $offset
            LIMIT $limit
            """,
            {
                "tenant_id": tenant_id,
                "not_started": AnalysisStatus.NOT_STARTED.value,
                "limit": limit,
                "offset": max(0, offset),
            },
        )

    def review_counts(self, tenant_id: str, matter_ref: str) -> Dict[int, Dict[str, int]]:
        """Redline decision counts for every version of a matter, by version number.

        One query rather than one per version: the matter page shows how far
        each round got, and the derived ``IN_REVIEW`` / ``REVIEWED`` status is
        exactly ``pending == 0`` on the latest one.
        """
        rows = self.graph.query(
            """
            MATCH (m:Matter {matter_ref: $matter_ref, tenant_id: $tenant_id})
                  -[r:HAS_VERSION]->(v:ContractVersion)
            OPTIONAL MATCH (v)-[:HAS_REDLINE]->(rl:Redline)
            WITH r.n AS n, coalesce(rl.status, 'PENDING') AS status, rl
            RETURN n,
                   count(rl) AS total,
                   sum(CASE WHEN rl IS NOT NULL AND status = 'PENDING' THEN 1 ELSE 0 END) AS pending,
                   sum(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) AS approved,
                   sum(CASE WHEN status = 'MODIFIED' THEN 1 ELSE 0 END) AS modified,
                   sum(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) AS rejected
            """,
            {"matter_ref": matter_ref, "tenant_id": tenant_id},
        )
        counts: Dict[int, Dict[str, int]] = {}
        for row in rows:
            if row.get("n") is None:
                continue
            counts[int(row["n"])] = {
                "total": int(row.get("total") or 0),
                "pending": int(row.get("pending") or 0),
                "approved": int(row.get("approved") or 0),
                "modified": int(row.get("modified") or 0),
                "rejected": int(row.get("rejected") or 0),
            }
        return counts

    def matter_for_version(self, tenant_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """Which matter a contract id belongs to, if any.

        Lets a bookmarked contract URL redirect to the matter it is now part of
        instead of dead-ending.
        """
        rows = self.graph.query(
            """
            MATCH (m:Matter {tenant_id: $tenant_id})-[r:HAS_VERSION]->
                  (v:ContractVersion {version_id: $version_id, tenant_id: $tenant_id})
            RETURN m.matter_ref AS matter_ref, m.title AS title, r.n AS n
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "version_id": version_id},
        )
        return rows[0] if rows else None

    def set_status(self, tenant_id: str, matter_ref: str, status: MatterStatus) -> Dict[str, Any]:
        """Write a transition a human made. Validation belongs to the caller."""
        rows = self.graph.query(
            """
            MATCH (m:Matter {matter_ref: $matter_ref, tenant_id: $tenant_id})
            SET m.status = $status, m.updated_at = datetime()
            RETURN m.matter_ref AS matter_ref, m.status AS status
            """,
            {"matter_ref": matter_ref, "tenant_id": tenant_id, "status": status.value},
        )
        if not rows:
            raise MatterNotFound(matter_ref)
        return rows[0]

    # -- migration --------------------------------------------------------

    def migrate_legacy_contracts(self, tenant_id: Optional[str] = None,
                                 dry_run: bool = False) -> Dict[str, Any]:
        """Give every contract that predates matters a matter to live in.

        Purely additive: it labels contracts and creates matters around them. It
        never writes to a `(:Redline)`, so the decisions recorded in Increment 4
        come through untouched — they hang off the same node, which has simply
        gained a label and a parent.

        **Repeat uploads of one document become rounds of one matter, not many
        matters.** The duplicate check never fired before this increment, so
        re-uploading a contract silently created a second unrelated `Contract` —
        and the history shows it: on the development database, 52 contracts are
        8 distinct documents, one of them uploaded 18 times. Filing those as 18
        matters would put the old bug on the landing page and bury the eight
        real contracts. They are grouped by source hash instead, oldest first,
        and nothing is discarded: every copy keeps its own redline decisions
        under its own version number.

        **Versions uploaded since this increment are left alone.** They already
        carry the `:ContractVersion` label, and one sitting unfiled is a
        confirmation card the reviewer has not answered yet — auto-filing it
        would burn a reference number for a decision they never made. A
        migration interrupted part-way is still picked up, because it marks its
        own work with `pending_migration` until the matter exists.

        Idempotent, so it is safe to re-run.
        """
        rows = self.graph.query(
            """
            MATCH (c:Contract)
            WHERE ($tenant_id IS NULL OR c.tenant_id = $tenant_id)
              AND NOT (:Matter)-[:HAS_VERSION]->(c)
              // Already a version and not filed means one of two things: a
              // confirmation card the reviewer has not answered — auto-filing
              // that would burn a reference on a decision they never made — or
              // this migration's own interrupted work, which `pending_migration`
              // marks so a retry picks it up.
              AND (NOT c:ContractVersion OR c.pending_migration = true)
              // The losing half of a duplicate race. Filing it would put back
              // the duplicate the constraint refused.
              AND c.superseded_by IS NULL
            RETURN c.file_id AS version_id,
                   c.tenant_id AS tenant_id,
                   c.contract_type AS contract_type,
                   c.full_text AS full_text,
                   c.summary AS summary,
                   toString(c.upload_date) AS upload_date,
                   [(p:Party)-[pr:PARTY_TO]->(c) | {name: p.name, role: pr.role}] AS parties
            ORDER BY c.upload_date, c.file_id
            """,
            {"tenant_id": tenant_id},
        )

        # Group by (tenant, source hash): one matter per distinct document, in
        # upload order, so the oldest copy becomes version 1 and the lowest
        # reference number goes to the oldest contract.
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in rows:
            contract_tenant = row.get("tenant_id") or "default-tenant"
            # The stored text is what the analysis has always read, so it is the
            # right thing to hash: a re-upload of the same PDF then matches the
            # migrated version rather than forking a second one.
            text = row.get("full_text") or row.get("summary") or ""
            if text.strip():
                digest = source_sha256(text)
            else:
                # No source at all. Hashing "" would make every textless legacy
                # contract in a tenant hash the same and collapse a pile of
                # unrelated documents into one matter — grouping on the absence
                # of evidence. They get an identity of their own instead, and no
                # `source_key`, so they never match a future upload either.
                digest = f"no-source:{row['version_id']}"
            groups.setdefault((contract_tenant, digest), []).append(row)

        planned: List[Dict[str, Any]] = []
        for (contract_tenant, digest), members in groups.items():
            first = members[0]
            counterparty = counterparty_from_parties(first.get("parties"))
            planned.append({
                "tenant_id": contract_tenant,
                "source_sha256": digest,
                "contract_type": first.get("contract_type") or "",
                "counterparty": counterparty,
                "title": suggest_title(first.get("contract_type"), counterparty,
                                       first["version_id"]),
                "version_ids": [m["version_id"] for m in members],
            })

        if dry_run:
            return {"migrated": 0, "versions": 0, "planned": planned, "dry_run": True}

        created: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        versions = 0
        for item in planned:
            version_ids = item["version_ids"]
            tenant = item["tenant_id"]
            try:
                for version_id in version_ids:
                    self._mark_pending_migration(tenant, version_id)
                    self.record_version(
                        tenant, version_id,
                        source_sha256=item["source_sha256"], filename=version_id,
                        # Repeat uploads of one document share a hash by
                        # definition, so they cannot all claim the unique key.
                        claim_source=False,
                    )

                # A previous run may have created the matter and then died
                # part-way through attaching the rest of the group. Every step
                # before the failure has committed — `graph.query` is one
                # transaction per call — so a retry that just created a second
                # matter would split one document across two references.
                # Recover the existing one and attach only what is missing.
                matter_ref = self._matter_holding_any(tenant, version_ids)
                resumed = matter_ref is not None

                if matter_ref is None:
                    result = self.create_matter(
                        tenant, version_ids[0],
                        title=item["title"],
                        counterparty=item["counterparty"],
                        contract_type=item["contract_type"],
                    )
                    matter_ref = result["matter_ref"]
                    versions += 1
                    remaining = version_ids[1:]
                else:
                    logger.info(
                        f"Resuming migration of {matter_ref}: it already holds part "
                        f"of this document"
                    )
                    remaining = [v for v in version_ids
                                 if self.matter_for_version(tenant, v) is None]

                # The remaining copies become later rounds of the same matter.
                # Each keeps its own redline decisions under its own number.
                for version_id in remaining:
                    self.attach_version(tenant, matter_ref, version_id)
                    versions += 1

                for version_id in version_ids:
                    self._clear_pending_migration(tenant, version_id)

                created.append({"matter_ref": matter_ref, "n": 1,
                                "versions": len(version_ids), "resumed": resumed})
            except Exception as e:
                # One bad document must not stop the rest from migrating.
                logger.error(f"Could not migrate {version_ids[0]}: {e}")
                failed.append({"version_id": version_ids[0], "error": str(e)})

        return {"migrated": len(created), "versions": versions, "created": created,
                "failed": failed, "dry_run": False}

    def _matter_holding_any(self, tenant_id: str, version_ids: List[str]) -> Optional[str]:
        """The matter a previous, interrupted run already made for this document."""
        rows = self.graph.query(
            """
            MATCH (m:Matter {tenant_id: $tenant_id})-[:HAS_VERSION]->(v:ContractVersion)
            WHERE v.version_id IN $version_ids
            RETURN m.matter_ref AS matter_ref
            LIMIT 1
            """,
            {"tenant_id": tenant_id, "version_ids": version_ids},
        )
        return rows[0]["matter_ref"] if rows else None

    def _mark_pending_migration(self, tenant_id: str, version_id: str) -> None:
        """Claim a contract for the migration before labelling it.

        Without this, a migration that dies between `record_version` and
        `create_matter` would leave a labelled but unfiled version that the next
        run skips — stranded, and invisible on the matters list.
        """
        self.graph.query(
            """
            MATCH (c:Contract {file_id: $version_id, tenant_id: $tenant_id})
            SET c.pending_migration = true
            """,
            {"version_id": version_id, "tenant_id": tenant_id},
        )

    def _clear_pending_migration(self, tenant_id: str, version_id: str) -> None:
        self.graph.query(
            """
            MATCH (c:Contract {file_id: $version_id, tenant_id: $tenant_id})
            REMOVE c.pending_migration
            """,
            {"version_id": version_id, "tenant_id": tenant_id},
        )
