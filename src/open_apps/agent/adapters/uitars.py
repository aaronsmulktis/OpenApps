"""UI-TARS adapter (default): <think>/<action> ReAct, raw pixel coordinates."""
from __future__ import annotations

from open_apps.agent.utils import flexible_parser

from .base import Adapter, AdapterResult


class UITarsAdapter(Adapter):
    def parse(self, response: str, viewport: tuple[int, int]) -> AdapterResult:
        return flexible_parser(response)
