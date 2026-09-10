import os
from unittest.mock import patch
import unittest
from fastapi.testclient import TestClient
from backend.main import app
from backend.governance.rbac import UserRole, Permission

class TestRBACIntegrated(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # These tests assert on RBAC status codes only. Handlers past the guard
        # may fail (no lifespan, so app.state.llm_manager is unset); we want that
        # surfaced as a 500 response rather than re-raised out of the client.
        self.any_client = TestClient(app, raise_server_exceptions=False)

    def test_admin_access_all(self):
        """ADMIN should have access to everything"""
        # Test Search
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.ADMIN},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.ADMIN}
        )
        self.assertNotEqual(response.status_code, 403)

    def test_viewer_restricted_access(self):
        """VIEWER should be restricted from sensitive actions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.VIEWER},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.VIEWER}
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("VIEW_AUDIT", response.json()["detail"])

    def test_legal_reviewer_access(self):
        """LEGAL_REVIEWER should have analysis and upload permissions"""
        # Test Search (ALLOWED)
        response = self.client.post(
            "/api/contracts/search/enhanced",
            headers={"X-User-Role": UserRole.LEGAL_REVIEWER},
            json={"search_level": "document", "query": "test"}
        )
        self.assertNotEqual(response.status_code, 403)
        
        # Test Audit Trail (DENIED)
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": UserRole.LEGAL_REVIEWER}
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_role_blocked(self):
        """Request with invalid role should be blocked (401)"""
        response = self.client.get(
            "/api/audit/trail/test-resource",
            headers={"X-User-Role": "HACKER"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid user role", response.json()["detail"])

    def test_missing_role_in_production_fails_closed(self):
        """No header in production means VIEWER — never ADMIN."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            # Search is allowed for VIEWER.
            response = self.client.post(
                "/api/contracts/search/enhanced",
                json={"search_level": "document", "query": "test"},
            )
            self.assertNotEqual(response.status_code, 403)

            # Audit is not.
            response = self.client.get("/api/audit/trail/test-resource")
            self.assertEqual(response.status_code, 403)

            # Nor is upload — the check that matters, since defaulting to ADMIN
            # left every write endpoint open to an unauthenticated caller.
            response = self.any_client.post("/api/documents/upload")
            self.assertEqual(response.status_code, 403)

    def test_missing_role_in_development_uses_configured_role(self):
        """Without real auth, dev falls back to DEV_DEFAULT_ROLE so the UI works."""
        with patch.dict(os.environ, {"ENVIRONMENT": "development",
                                     "DEV_DEFAULT_ROLE": "LEGAL_REVIEWER"}):
            # LEGAL_REVIEWER may upload: not a 403. (No file attached, so FastAPI
            # rejects the body with 422 — which proves RBAC let it through.)
            response = self.any_client.post("/api/documents/upload")
            self.assertNotEqual(response.status_code, 403)

            # But still cannot read the audit trail.
            response = self.client.get("/api/audit/trail/test-resource")
            self.assertEqual(response.status_code, 403)

    def test_invalid_dev_default_role_falls_back_to_viewer(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development",
                                     "DEV_DEFAULT_ROLE": "SUPERUSER"}):
            response = self.any_client.post("/api/documents/upload")
            self.assertEqual(response.status_code, 403)

if __name__ == "__main__":
    unittest.main()
