"""Exceptions raised by the AI gateway. Messages never contain client data."""

from __future__ import annotations


class AIError(Exception):
    """Base class for AI-gateway errors."""


class AIDisabledError(AIError):
    """The provider is ``none`` (the default) or AI is switched off for the engagement."""


class AIConfigError(AIError):
    """Provider configuration is incomplete or not allowed (e.g. missing region)."""


class PayloadError(AIError):
    """The payload cannot be built (e.g. full statements requested without the explicit flag)."""


class ApprovalError(AIError):
    """Approval missing, expired, tampered with, bound to another payload, or self-approved."""

    def __init__(self, message: str, record: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.record = record or {}


class BudgetExceededError(AIError):
    """The monthly AI budget for the firm or engagement would be exceeded (E-AI-04)."""
