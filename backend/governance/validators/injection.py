import re
from typing import List, Optional, Dict, Any
from ..base import IGuardValidator, GuardResult

class InjectionValidator(IGuardValidator):
    """Detects prompt injection and jailbreak attempts"""
    
    PATTERNS = [
        r"(?i)ignore\s+(all\s+)?previous\s+instructions",
        r"(?i)system\s+override",
        r"(?i)you\s+are\s+now\s+a\s+DAN",
        r"(?i)jailbreak",
        r"(?i)forget\s+everything",
        r"(?i)disregard\s+prior\s+rules",
        r"(?i)output\s+the\s+system\s+prompt",
        r"(?i)reveal\s+your\s+instructions"
    ]

    def validate(self, input_text: str, context: Optional[Dict[str, Any]] = None) -> GuardResult:
        for pattern in self.PATTERNS:
            # `input_text`, not `prompt`: the undefined name raised a NameError
            # on the first pattern of every chat turn, which killed the stream
            # before a single token and left the UI on "Failed to generate the
            # response" — the guard blocked everything, injection or not.
            if re.search(pattern, input_text):
                return GuardResult(
                    is_safe=False,
                    violation_type="PROMPT_INJECTION",
                    message="Potential prompt injection attempt detected.",
                    metadata={"pattern_matched": pattern}
                )
        return GuardResult(is_safe=True)
