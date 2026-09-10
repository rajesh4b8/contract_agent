"""
Test Pattern Integration - ReACT and Chain-of-Thought
Tests pattern agents, selector, and orchestrator integration.
"""

import pytest
import asyncio
from backend.agents.patterns.react_agent import ReACTAgent
from backend.agents.patterns.chain_of_thought_agent import ChainOfThoughtAgent
from backend.agents.patterns.pattern_selector import PatternSelector, AnalysisComplexity


class TestReACTAgent:
    """Test ReACT pattern agent"""
    
    @pytest.mark.asyncio
    async def test_react_agent_basic_execution(self):
        """Test basic ReACT agent execution"""
        agent = ReACTAgent(max_iterations=2)
        
        result = await agent.execute({
            'contract_text': 'Sample contract with termination clause and liability terms...',
            'contract_id': 'test_001'
        })
        
        assert result['success'] == True
        assert 'steps' in result
        assert result['pattern'] == 'ReACT'
        assert len(result['steps']) <= 2
        assert 'final_confidence' in result
    
    @pytest.mark.asyncio
    async def test_react_agent_convergence(self):
        """Test ReACT agent converges with high confidence"""
        agent = ReACTAgent(max_iterations=5)
        
        result = await agent.execute({
            'contract_text': 'This contract contains payment terms, liability clauses, and termination provisions.',
            'contract_id': 'test_002'
        })
        
        assert result['success'] == True
        assert result['final_confidence'] > 0.0
    
    @pytest.mark.asyncio
    async def test_react_agent_error_handling(self):
        """Test ReACT agent handles missing data"""
        agent = ReACTAgent()
        
        result = await agent.execute({
            'contract_id': 'test_003'
        })
        
        assert result['success'] == False
        assert 'error' in result


class TestChainOfThoughtAgent:
    """Test Chain-of-Thought pattern agent"""
    
    @pytest.mark.asyncio
    @pytest.mark.integration  # reads policies from Neo4j
    async def test_cot_agent_risk_assessment(self):
        """Test CoT agent risk assessment"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'clauses': [
                {'clause_type': 'Payment Terms', 'content': 'Payment due in 60 days'},
                {'clause_type': 'Liability', 'content': 'Unlimited liability'}
            ],
            'task_type': 'risk_assessment',
            'contract_id': 'test_004'
        })
        
        assert result['success'] == True
        assert 'thought_chain' in result
        assert result['pattern'] == 'Chain-of-Thought'
        assert 'final_result' in result
        assert len(result['thought_chain']) > 0
    
    @pytest.mark.asyncio
    async def test_cot_agent_clause_analysis(self):
        """Test CoT agent clause analysis"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'contract_text': 'Sample contract with various clauses...',
            'target_clause': 'termination',
            'task_type': 'clause_analysis',
            'contract_id': 'test_005'
        })
        
        assert result['success'] == True
        assert 'thought_chain' in result


class TestPatternSelector:
    """Test pattern selector logic"""
    
    def test_selector_complex_contract(self):
        """Test selector chooses ReACT for complex contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 60000,  # Large contract
            'clauses': [{'type': 'test'}] * 25,
            'violations': []
        })
        
        assert pattern == 'react'
    
    def test_selector_moderate_contract(self):
        """Test selector chooses CoT for moderate contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 15000,
            'clauses': [{'type': 'test'}] * 12,
            'violations': []
        })
        
        assert pattern == 'chain_of_thought'
    
    def test_selector_simple_contract(self):
        """Test selector uses standard for simple contracts"""
        pattern = PatternSelector.select_pattern({
            'contract_text': 'x' * 5000,
            'clauses': [{'type': 'test'}] * 5,
            'violations': []
        })
        
        assert pattern == 'standard'
    
    def test_complexity_assessment(self):
        """Test complexity assessment logic"""
        # Complex
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 60000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.COMPLEX
        
        # Moderate
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 15000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.MODERATE
        
        # Simple
        complexity = PatternSelector._assess_complexity({
            'contract_text': 'x' * 5000,
            'clauses': [],
            'violations': []
        })
        assert complexity == AnalysisComplexity.SIMPLE


class TestOrchestratorIntegration:
    """Test pattern integration with orchestrator"""
    
    def test_intelligence_state_has_pattern_fields(self):
        """Test IntelligenceState includes pattern fields"""
        from backend.agents.intelligence_state import IntelligenceState
        
        # Verify pattern fields exist in type hints
        annotations = IntelligenceState.__annotations__
        assert 'pattern_used' in annotations
        assert 'pattern_analysis' in annotations
    
    @pytest.mark.asyncio
    async def test_pattern_analysis_workflow_node(self):
        """Test pattern_analysis node exists in workflow"""
        from backend.agents.contract_intelligence_agents import IntelligenceOrchestrator
        
        orchestrator = IntelligenceOrchestrator(llm=None)
        
        # Verify _pattern_analysis method exists
        assert hasattr(orchestrator, '_pattern_analysis')
        assert callable(orchestrator._pattern_analysis)


class TestLoggingIntegration:
    """Test centralized logging integration"""
    
    @pytest.mark.asyncio
    async def test_agents_use_audit_logger(self):
        """Test agents use AuditLogger"""
        agent = ReACTAgent()
        
        # Verify audit_logger exists
        assert hasattr(agent, 'audit_logger')
        assert agent.audit_logger is not None
    
    @pytest.mark.asyncio
    async def test_agents_use_workflow_tracker(self):
        """Test agents use workflow tracker"""
        agent = ChainOfThoughtAgent()
        
        result = await agent.execute({
            'clauses': [],
            'contract_id': 'test_logging'
        })
        
        # Execution should be tracked
        assert agent.execution is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
