from backend.shared.utils.logger import get_logger
from backend.shared.config.models import (
    LEGACY_ALIASES,
    build_llm,
    configured_model_ids,
    normalize_model_id,
)

logger = get_logger(__name__)

from backend.contract_chat_agent import get_agent

from typing import Any


class LLMManager:
    agents = {}

    def __init__(self):
        self.init_agents()

    def init_agents(self):
        """Build one chat agent per configured model id.

        The model catalogue (ids, provider model strings, which providers are
        enabled) lives in ``backend.shared.config.models``.
        """
        self.agents = {}
        for model_id in configured_model_ids():
            try:
                self.agents[model_id] = get_agent(build_llm(model_id))
            except Exception as e:  # noqa: BLE001 - one bad provider shouldn't kill the rest
                logger.warning(f"Skipping model '{model_id}': {e}")

        # Keep legacy ids resolving to the same agent objects.
        for old_id, new_id in LEGACY_ALIASES.items():
            if new_id in self.agents and old_id not in self.agents:
                self.agents[old_id] = self.agents[new_id]

        logger.info(f"Loaded {len(set(self.agents.values()))} llms ({len(self.agents)} ids).")

    def get_model_by_name(self, name: str):
        agent = self.agents.get(name) or self.agents.get(normalize_model_id(name))
        if agent is None:
            raise ValueError(f"The model {name} wasn't initiated")
        return agent
