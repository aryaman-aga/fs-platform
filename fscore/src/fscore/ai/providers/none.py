"""Provider ``none`` (the default): AI is off and nothing is ever sent."""

from __future__ import annotations

from fscore.ai.errors import AIDisabledError
from fscore.ai.provider import ProviderRequest, RawCompletion
from fscore.ai.settings import ProviderSettings


class NoneProvider:
    name = "none"

    def send(self, request: ProviderRequest, settings: ProviderSettings) -> RawCompletion:
        raise AIDisabledError("AI review is disabled (provider 'none')")
