"""Self-service developer onboarding (v1).

Public surface:

    backend.onboarding.service      application → verification → provisioning
    backend.onboarding.notifications durable email queue + retry
    backend.onboarding.email        transport interface (SMTP / not configured)
    backend.onboarding.store        schema + persistence
    backend.onboarding.config       environment configuration + state vocabulary
"""

from __future__ import annotations

__all__ = ["config", "email", "keys", "notifications", "service", "store"]
