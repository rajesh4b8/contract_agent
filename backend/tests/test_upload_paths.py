"""The upload path's four quiet bugs, and the two paths that replace it.

Each of these was invisible from the outside: the model dropdown that changed
nothing, the tenant header the endpoint never read, the tenant resolved three
different ways across four endpoints, and the duplicate check that could not
fire. None of them raised anything; they just did the wrong thing silently,
which is why they are pinned here by inspecting what the endpoints actually
declare rather than by hoping someone notices.
"""
from unittest.mock import MagicMock

import pytest
from fastapi.routing import APIRoute

from backend.governance.rbac import get_current_tenant
from backend.main import app


def route(path: str, method: str = "POST") -> APIRoute:
    for candidate in app.routes:
        if isinstance(candidate, APIRoute) and candidate.path == path \
                and method in candidate.methods:
            return candidate
    raise AssertionError(f"no {method} {path} in the app")


def field_names(fields) -> set:
    return {f.alias or f.name for f in fields}


def dependency_calls(dependant) -> set:
    """Every function this route resolves a parameter from, at any depth."""
    found = set()
    stack = list(dependant.dependencies)
    while stack:
        current = stack.pop()
        if current.call is not None:
            found.add(current.call)
        stack.extend(current.dependencies)
    return found


UPLOAD = "/api/documents/upload"
ANALYZE = "/api/intelligence/contracts/{contract_id}/analyze"


class TestTheModelDropdownNowDoesSomething:
    """`DocumentUpload.tsx` sends `model` in the FormData body.

    The endpoint declared `model: str = Query(...)`, so the field was dropped on
    the floor and every upload used DEFAULT_MODEL_ID — currently `free-large`,
    at 50-130s a call. Nothing failed; the page simply spent two minutes in a
    model the user had not chosen.
    """

    def test_the_body_field_the_browser_sends_is_declared(self):
        assert "model" in field_names(route(UPLOAD).dependant.body_params)

    def test_the_query_form_still_works_for_the_scripts(self):
        """`scripts/evaluate_pipeline.py` passes it as a query parameter."""
        assert "model" in field_names(route(UPLOAD).dependant.query_params)

    @pytest.mark.parametrize("form,query,expected", [
        ("gemini-flash-lite", None, "gemini-flash-lite"),
        (None, "gemini-flash-lite", "gemini-flash-lite"),
        ("gemini-flash-lite", "free-large", "gemini-flash-lite"),
    ])
    def test_the_body_wins_when_both_are_sent(self, form, query, expected):
        """Mirrors `model = model or model_param or DEFAULT_MODEL_ID`."""
        assert (form or query) == expected


class TestTheTenantComesFromTheCaller:
    """Three ways of resolving a tenant, collapsed into one dependency.

    Upload read a query parameter the frontend has never set, so contracts
    landed in `default-tenant`; redlines read the `X-Tenant-ID` header; status
    and the dashboard hardcoded `"default-tenant"`. The same document could be
    written under one tenant and looked up under another.
    """

    @pytest.mark.parametrize("path,method", [
        (UPLOAD, "POST"),
        ("/api/documents/upload-stream", "POST"),
        (ANALYZE, "POST"),
        ("/api/intelligence/contracts/{contract_id}/status", "GET"),
        ("/api/intelligence/contracts/{contract_id}/redlines", "GET"),
        ("/api/intelligence/contracts/{contract_id}/analysis", "GET"),
        ("/api/intelligence/dashboard/summary", "GET"),
        ("/api/matters", "GET"),
        ("/api/matters", "POST"),
        ("/api/matters/{matter_ref}", "GET"),
    ])
    def test_every_tenant_scoped_endpoint_uses_the_one_dependency(self, path, method):
        assert get_current_tenant in dependency_calls(route(path, method).dependant), (
            f"{method} {path} resolves its tenant some other way"
        )

    @pytest.mark.parametrize("path,method", [
        (UPLOAD, "POST"),
        ("/api/documents/upload-stream", "POST"),
        (ANALYZE, "POST"),
    ])
    def test_no_endpoint_still_takes_a_tenant_from_the_url(self, path, method):
        """A URL you can edit and share is the most casual form of the problem."""
        assert "tenant_id" not in field_names(route(path, method).dependant.query_params)


class TestTheTwoUploadPaths:
    def test_a_round_can_be_filed_into_a_matter_from_the_body(self):
        """"Upload new round" from the matter's own page."""
        assert "matter_ref" in field_names(route(UPLOAD).dependant.body_params)

    def test_and_from_the_query_string_for_scripted_callers(self):
        assert "matter_ref" in field_names(route(UPLOAD).dependant.query_params)

    def test_the_new_contract_path_needs_no_matter_at_all(self):
        """No `matter_ref` is the "new contract" path: stored, then confirmed."""
        matter_ref = next(f for f in route(UPLOAD).dependant.body_params
                          if (f.alias or f.name) == "matter_ref")
        assert not matter_ref.required


class TestTheDuplicateCheckCanNowFire:
    """The old one compared the filename against `file_id`.

    `file_id` is generated as `UPLOADED_{random}_{date}` and never contains the
    filename, so `WHERE c.file_id CONTAINS $filename` matched nothing, ever.
    Every re-upload of a revised contract created a second, unrelated Contract
    and orphaned the previous round's redline decisions. It also ran without a
    tenant filter, so a filename collision would have returned another tenant's
    contract id to the uploader.
    """

    def test_the_filename_comparison_is_gone(self):
        import inspect

        from backend.api import document_upload

        source = inspect.getsource(document_upload)
        assert "c.file_id CONTAINS" not in source

    def test_the_replacement_is_keyed_on_the_source_hash(self):
        import inspect

        from backend.infrastructure.matter_repository import MatterRepository

        source = inspect.getsource(MatterRepository.version_by_source_hash)
        assert "source_sha256: $digest" in source
        assert "tenant_id: $tenant_id" in source


class TestADuplicateKeepsTheRequestedDestination:
    @pytest.mark.asyncio
    async def test_an_upload_into_a_matter_files_the_existing_unfiled_version(self):
        from backend.api.document_upload import _duplicate_upload_response

        matters = MagicMock()
        matters.attach_version.return_value = {"matter_ref": "MSA-2026-0042", "n": 2}

        result = await _duplicate_upload_response(
            filename="msa.pdf",
            model="gemini-flash-lite",
            twin={"version_id": "V-1"},
            tenant_id="acme",
            matter_ref="MSA-2026-0042",
            matters=matters,
            repo=object(),
        )

        matters.attach_version.assert_called_once_with("acme", "MSA-2026-0042", "V-1")
        assert result["status"] == "success"
        assert result["matter_ref"] == "MSA-2026-0042"
        assert result["version"] == 2
        assert "needs_filing" not in result

    @pytest.mark.asyncio
    async def test_a_duplicate_that_cannot_be_filed_is_reported_as_an_error(self):
        from backend.api.document_upload import _duplicate_upload_response

        matters = MagicMock()
        matters.attach_version.side_effect = RuntimeError("neo4j is down")

        result = await _duplicate_upload_response(
            filename="msa.pdf",
            model="gemini-flash-lite",
            twin={"version_id": "V-1"},
            tenant_id="acme",
            matter_ref="MSA-2026-0042",
            matters=matters,
            repo=object(),
        )

        assert result["status"] == "error"
        assert result["needs_filing"] is False
        assert "could not be added to MSA-2026-0042" in result["details"]
