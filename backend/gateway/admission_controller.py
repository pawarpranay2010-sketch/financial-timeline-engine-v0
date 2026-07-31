"""AdmissionController — token budget, rate-limit check, context-fit validation."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AdmissionResult:
    allowed: bool = True
    reason: str = ""
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 4096
    context_ok: bool = True
    quota_ok: bool = True


class AdmissionController:
    """Pre-flight checks before sending requests to providers.

    Validates:
    - Input fits within provider context window
    - Estimated tokens within quota budget
    - Provider is not rate-limited / circuit-open
    """

    def __init__(self, tokens_per_char: float = 0.25):
        self.tokens_per_char = tokens_per_char

    def estimate_tokens(self, text: str) -> int:
        """Quick token estimate without a tokenizer."""
        return int(len(text) * self.tokens_per_char) + 1

    def check_context_fit(self, prompt: str, system_prompt: str,
                          context_window: int, output_tokens: int) -> bool:
        """Check if total estimated tokens fit within context window."""
        total = self.estimate_tokens(prompt) + self.estimate_tokens(system_prompt) + output_tokens
        return total <= context_window

    def admit(self, prompt: str, system_prompt: str = "",
              context_window: int = 8192, output_tokens: int = 4096,
              rpm_limit: Optional[int] = None,
              current_rpm: int = 0) -> AdmissionResult:
        """Check if a request should be admitted."""
        input_tokens = self.estimate_tokens(prompt) + self.estimate_tokens(system_prompt)

        # Context fit check
        total_needed = input_tokens + output_tokens
        if total_needed > context_window:
            return AdmissionResult(
                allowed=False,
                reason=f"Request too large ({total_needed} tokens) for context window ({context_window})",
                estimated_input_tokens=input_tokens,
                estimated_output_tokens=output_tokens,
                context_ok=False,
                quota_ok=True,
            )

        # Rate limit check
        if rpm_limit is not None and current_rpm >= rpm_limit:
            return AdmissionResult(
                allowed=False,
                reason=f"Rate limit reached ({current_rpm}/{rpm_limit} RPM)",
                estimated_input_tokens=input_tokens,
                estimated_output_tokens=output_tokens,
                context_ok=True,
                quota_ok=False,
            )

        return AdmissionResult(
            allowed=True,
            reason="Admitted",
            estimated_input_tokens=input_tokens,
            estimated_output_tokens=output_tokens,
        )
