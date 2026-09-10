import unittest
import json
import asyncio
from unittest.mock import MagicMock, patch

# The Neo4j and Gemini singletons are lazy (backend/shared/utils/lazy.py), so a
# plain import touches no external service and needs no import-time patching.
from backend.mcp_server import search_clause_library, get_playbook_rule, fetch_contract_metadata
from backend.shared.utils.mcp_logger import trace_id_var

class TestMCPCapabilities(unittest.IsolatedAsyncioTestCase):
    """
    Verification tests for the MCP layer (SOLID, DRY, Tracing).
    """

    def setUp(self):
        # Reset trace_id for each test
        trace_id_var.set(None)

    async def test_missing_tenant_id(self):
        """Verify that tenant_id is mandatory (Security/Access Control requirement)"""
        # Calling tool without tenant_id in kwargs
        result_json = await search_clause_library(query="test")
        result = json.loads(result_json)
        
        self.assertIn("error", result)
        self.assertEqual(result["error"], "Missing mandatory 'tenant_id' parameter")

    @patch("backend.mcp_server.get_policy_repo")
    async def test_search_clause_library_success(self, mock_get_repo):
        """Verify successful clause search with tracing"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.search_policies_semantic.return_value = [{"id": "c1", "text": "Sample clause"}]
        
        result_json = await search_clause_library(query="liability", tenant_id="test_tenant")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertEqual(len(result["clauses"]), 1)
        # Ensure trace_id was generated
        self.assertIsNotNone(trace_id_var.get())

    @patch("backend.mcp_server.get_policy_repo")
    async def test_get_playbook_rule_success(self, mock_get_repo):
        """Verify playbook rule retrieval"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_rule = MagicMock()
        mock_rule.id = "r1"
        mock_rule.rule_text = "Standard rule"
        mock_rule.severity = "HIGH"
        mock_rule.rule_type = "compliance"
        
        mock_repo.get_applicable_policies.return_value = [mock_rule]
        
        result_json = await get_playbook_rule(tenant_id="test_tenant", contract_type="MSA")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertEqual(result["rules"][0]["text"], "Standard rule")

    @patch("backend.mcp_server.get_contract_repo")
    async def test_fetch_metadata_success(self, mock_get_repo):
        """Verify metadata fetching"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        mock_repo.get_contract_by_id.return_value = {"file_id": "CNT1", "parties": []}
        
        result_json = await fetch_contract_metadata(contract_id="CNT1", tenant_id="test_tenant")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertEqual(result["metadata"]["file_id"], "CNT1")

    @patch("backend.mcp_server.get_contract_repo")
    async def test_cross_tenant_access_denied(self, mock_get_repo):
        """Verify that accessing a contract with the wrong tenant_id fails (Data Isolation)"""
        mock_repo = MagicMock()
        mock_get_repo.return_value = mock_repo
        # Simulate repository returning None when tenant_id doesn't match
        mock_repo.get_contract_by_id.return_value = None
        
        result_json = await fetch_contract_metadata(contract_id="CNT1", tenant_id="wrong_tenant")
        result = json.loads(result_json)
        
        self.assertFalse(result["success"])
        self.assertIn("not found for tenant wrong_tenant", result["error"])

if __name__ == "__main__":
    unittest.main()
