"""ReACT Pattern Agent - Reasoning-Action-Observation cycles with SOLID principles."""

from typing import Dict, Any, List
from dataclasses import dataclass
from .base_pattern_agent import BasePatternAgent
from backend.agents.intelligence_tools import ClauseDetectorTool
import json
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


@dataclass
class ReACTStep:
    reasoning: str
    action: str
    observation: str
    confidence: float
    iteration: int



class ReACTAgent(BasePatternAgent):
    """ReACT Pattern: Reasoning-Action-Observation (SOLID: SRP, OCP, DIP)"""
    
    def __init__(self, max_iterations: int = 3, llm=None):
        super().__init__("ReACT Pattern Agent")
        self.max_iterations = max_iterations
        self.steps: List[ReACTStep] = []
        # Needs the model: without it the tool cannot extract clauses.
        self.clause_tool = ClauseDetectorTool(llm=llm)
    
    def get_agent_role(self) -> str:
        return "Iterative contract analysis with reasoning-action-observation cycles"
    
    def get_pattern_name(self) -> str:
        return "ReACT"
    
    async def _execute_pattern(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """ReACT-specific logic reusing existing tools (DRY principle)"""
        contract_text = context.get('contract_text', '')
        
        if not contract_text:
            return {'success': False, 'error': 'Missing contract text'}
        
        self.steps = []
        working_context = {'contract_text': contract_text, 'findings': []}
        
        for iteration in range(self.max_iterations):
            step = await self._execute_react_cycle(working_context, iteration)
            self.steps.append(step)
            
            logger.info(f"ReACT iteration {iteration}: confidence={step.confidence:.2f}")
            
            if step.confidence >= 0.8:
                logger.info(f"ReACT converged at iteration {iteration}")
                break
            
            working_context['previous_steps'] = self.steps
        
        return {
            'success': True,
            'pattern': 'ReACT',
            'steps': [self._step_to_dict(step) for step in self.steps],
            'final_confidence': self.steps[-1].confidence if self.steps else 0.0,
            'iterations': len(self.steps)
        }
    
    async def _execute_react_cycle(self, context: Dict[str, Any], iteration: int) -> ReACTStep:
        reasoning = self._generate_reasoning(context, iteration)
        action_desc, observation = await self._execute_action(context, reasoning, iteration)
        confidence = self._calculate_confidence(observation, iteration)
        
        return ReACTStep(reasoning, action_desc, observation, confidence, iteration)
    
    def _generate_reasoning(self, context: Dict[str, Any], iteration: int) -> str:
        query = context.get('original_query', '')
        
        if iteration == 0:
            return f"Initial analysis: Need to find '{query}' in contract. Starting broad search."
        else:
            return f"Previous search needs refinement. Focusing on more specific terms."
    
    async def _execute_action(self, context: Dict[str, Any], reasoning: str, iteration: int) -> tuple[str, str]:
        """Execute action using existing ClauseDetectorTool (DRY)"""
        contract_text = context.get('contract_text', '')
        
        # Reuse existing tool instead of custom action
        clauses_json = self.clause_tool._run(contract_text)
        clauses = json.loads(clauses_json)
        
        action_desc = f"Clause detection (iteration {iteration})"
        observation = f"Found {len(clauses)} clauses"
        
        context['findings'] = clauses
        
        return action_desc, observation
    
    def _get_refined_terms(self, query: str) -> List[str]:
        if 'termination' in query.lower():
            return ['termination rights', 'notice period', 'termination clause']
        elif 'liability' in query.lower():
            return ['liability cap', 'limitation of liability', 'damages']
        return [query, f"{query} clause"]
    
    def _extract_findings(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        findings = []
        for search_result in result.get('search_results', []):
            for match in search_result.get('matches', []):
                findings.append({
                    'type': 'clause_match',
                    'content': match['content'],
                    'line_number': match['line_number']
                })
        return findings
    
    def _generate_observation(self, result: Dict[str, Any]) -> str:
        total_matches = result.get('total_matches', 0)
        
        if total_matches == 0:
            return "No matches found. Need broader search terms."
        elif total_matches > 10:
            return f"Found {total_matches} matches, many irrelevant. Need refinement."
        else:
            return f"Found {total_matches} relevant matches."
    
    def _calculate_confidence(self, observation: str, iteration: int) -> float:
        base_confidence = 0.3 + (iteration * 0.2)
        
        if 'no matches' in observation.lower():
            return max(0.1, base_confidence - 0.3)
        elif 'irrelevant' in observation.lower():
            return max(0.4, base_confidence)
        elif 'relevant' in observation.lower():
            return min(0.9, base_confidence + 0.4)
        
        return base_confidence
    
    def _step_to_dict(self, step: ReACTStep) -> Dict[str, Any]:
        return {
            'iteration': step.iteration,
            'reasoning': step.reasoning,
            'action': step.action,
            'observation': step.observation,
            'confidence': step.confidence
        }