import json
import logging
from datetime import datetime
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class AgentStatus(Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"

@dataclass
class AgentExecution:
    agent_name: str
    agent_role: str
    start_time: datetime
    end_time: datetime = None
    status: AgentStatus = AgentStatus.PROCESSING
    input_summary: str = ""
    output_summary: str = ""
    processing_time_ms: int = 0
    error_message: str = None

class WorkflowTracker:
    """Executive dashboard for multi-agent workflow visibility"""
    
    def __init__(self):
        self.executions: List[AgentExecution] = []
        self.workflow_start_time = None
        
    def start_workflow(self):
        """Start tracking a new workflow"""
        self.workflow_start_time = datetime.now()
        self.executions = []
        logger.info("🚀 MULTI-AGENT CONTRACT ANALYSIS WORKFLOW STARTED")
        
    def start_agent(self, agent_name: str, agent_role: str, input_summary: str) -> AgentExecution:
        """Start tracking an agent execution"""
        execution = AgentExecution(
            agent_name=agent_name,
            agent_role=agent_role,
            start_time=datetime.now(),
            input_summary=input_summary
        )
        self.executions.append(execution)
        
        logger.info(f"🤖 AGENT STARTED: {agent_name}")
        logger.info(f"   Role: {agent_role}")
        logger.info(f"   Input: {input_summary}")
        
        return execution
        
    def complete_agent(self, execution: AgentExecution, output_summary: str):
        """Complete an agent execution"""
        execution.end_time = datetime.now()
        execution.status = AgentStatus.COMPLETED
        execution.output_summary = output_summary
        execution.processing_time_ms = int((execution.end_time - execution.start_time).total_seconds() * 1000)
        
        logger.info(f"✅ AGENT COMPLETED: {execution.agent_name}")
        logger.info(f"   Output: {output_summary}")
        logger.info(f"   Processing Time: {execution.processing_time_ms}ms")
        
    def error_agent(self, execution: AgentExecution, error_message: str):
        """Mark an agent execution as failed"""
        execution.end_time = datetime.now()
        execution.status = AgentStatus.ERROR
        execution.error_message = error_message
        execution.processing_time_ms = int((execution.end_time - execution.start_time).total_seconds() * 1000)
        
        logger.error(f"❌ AGENT FAILED: {execution.agent_name}")
        logger.error(f"   Error: {error_message}")
        
    def complete_workflow(self):
        """Complete the workflow tracking"""
        if self.workflow_start_time is None:
            # Some paths (the planning engine) complete a workflow they never
            # started. Raising here masked the real failure underneath with a
            # confusing TypeError about datetime and None.
            logger.warning("complete_workflow() called without start_workflow(); timing unavailable")
            self.workflow_start_time = datetime.now()

        total_time = int((datetime.now() - self.workflow_start_time).total_seconds() * 1000)
        
        logger.info("🏁 MULTI-AGENT WORKFLOW COMPLETED")
        logger.info(f"   Total Processing Time: {total_time}ms")
        logger.info(f"   Agents Executed: {len(self.executions)}")
        
        # Print executive summary
        self._print_executive_summary()
        
    def _print_executive_summary(self):
        """Print executive summary of agent workflow"""
        logger.info("=" * 80)
        logger.info("📊 EXECUTIVE WORKFLOW SUMMARY - CONTRACT INTELLIGENCE ANALYSIS")
        logger.info("=" * 80)
        
        for i, execution in enumerate(self.executions, 1):
            status_emoji = "✅" if execution.status == AgentStatus.COMPLETED else "❌"
            
            logger.info(f"{i}. {status_emoji} {execution.agent_name}")
            logger.info(f"   Role: {execution.agent_role}")
            logger.info(f"   Input: {execution.input_summary}")
            logger.info(f"   Output: {execution.output_summary}")
            logger.info(f"   Time: {execution.processing_time_ms}ms")
            if execution.error_message:
                logger.info(f"   Error: {execution.error_message}")
            logger.info("")
        
        # Data flow summary
        logger.info("🔄 AGENT DATA FLOW:")
        for i in range(len(self.executions) - 1):
            current = self.executions[i]
            next_agent = self.executions[i + 1]
            logger.info(f"   {current.agent_name} → {next_agent.agent_name}")
            logger.info(f"   Data: {current.output_summary} → {next_agent.input_summary}")
        
        logger.info("=" * 80)

    def get_workflow_status(self) -> Dict[str, Any]:
        """Get current workflow status for API responses"""
        return {
            "total_agents": len(self.executions),
            "completed_agents": len([e for e in self.executions if e.status == AgentStatus.COMPLETED]),
            "failed_agents": len([e for e in self.executions if e.status == AgentStatus.ERROR]),
            "agent_executions": [{
                "agent_name": e.agent_name,
                "agent_role": e.agent_role,
                "status": e.status.value,
                "input_summary": e.input_summary,
                "output_summary": e.output_summary,
                "processing_time_ms": e.processing_time_ms,
                "error_message": e.error_message
            } for e in self.executions],
            "data_flow": [{
                "from_agent": self.executions[i].agent_name,
                "to_agent": self.executions[i + 1].agent_name,
                "data_transferred": self.executions[i].output_summary
            } for i in range(len(self.executions) - 1)]
        }

# Global tracker instance
workflow_tracker = WorkflowTracker()

def get_current_workflow_status():
    """Get current workflow status for API"""
    return workflow_tracker.get_workflow_status()