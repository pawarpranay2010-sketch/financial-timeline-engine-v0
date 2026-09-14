"""
Platrixa — public developer interface (Phase 12)
================================================

The public interface is a thin boundary over the Platrixa Kernel.

    from platrixa import Platrixa

    client = Platrixa()
    result = client.process("Purchased furniture for cash ₹15,000")
    print(result.status)                 # VERIFIED / REVIEW_REQUIRED / ...

What this package is:

- ONE clean entry point (``Platrixa``) that configures and invokes the
  authoritative Kernel exactly once per request.
- A stable, deterministic result view (``PlatrixaResult``) and a small
  error contract (``InputError`` / ``ProviderError``).
- Explicit, frozen configuration (``PlatrixaConfig``).

What this package is NOT:

- It is not a second accounting engine, grounding implementation, model
  provider, rule engine, or persistence layer. It contains none of that
  logic; it forwards to the Kernel, which owns all of it.
- It is not a hosted service: there is no auth, no API keys, no server.
  (The FastAPI deployment in ``api/`` is the product HTTP boundary.)

State authority is unchanged: VERIFIED is produced only by the
deterministic accounting flow inside ``Kernel.process``. The optional
Phase 10 rule boundary (YAML packs + Python hooks) can only downgrade a
deterministic success state — never upgrade one, never manufacture
VERIFIED.

Importing this package is cheap: no model, no network, no database, and
no heavyweight scientific stack is loaded at import time.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import PROVIDERS, TRANSPORTS, PlatrixaConfig
from .errors import InputError, PlatrixaError, ProviderError
from ._facade import Platrixa, PlatrixaResult

# Status constants are re-exported from the Kernel's declared public
# contract surface (backend/kernel/result.py) so developers can compare
# result.status against stable names without importing internals.
from backend.kernel.result import (  # noqa: E402
    FORBIDDEN_OUTPUT,
    GROUNDING_FAILED,
    MODEL_UNAVAILABLE,
    UNSUPPORTED_TRANSACTION,
    VALIDATION_FAILED,
)

try:  # accounting-authority states live in the maths status module
    from backend.maths.status import BLOCKED, REVIEW_REQUIRED, VERIFIED
except Exception:  # pragma: no cover - mirror of kernel.py's fallback
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    VERIFIED = "VERIFIED"

__all__ = [
    "__version__",
    # client + result
    "Platrixa",
    "PlatrixaResult",
    # configuration
    "PlatrixaConfig",
    "PROVIDERS",
    "TRANSPORTS",
    # errors
    "PlatrixaError",
    "InputError",
    "ProviderError",
    # terminal states (re-exported, stable names)
    "VERIFIED",
    "REVIEW_REQUIRED",
    "BLOCKED",
    "MODEL_UNAVAILABLE",
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "FORBIDDEN_OUTPUT",
    "UNSUPPORTED_TRANSACTION",
]
