"""Per-model-family adapter contract: response parsing + coordinate conversion.

Prompts live in the agent yaml, not here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class AdapterResult(TypedDict, total=False):
    action: str
    displayed_action: str
    think: str | None


@dataclass
class Adapter:
    def default_prompts(self) -> dict:
        return {}

    def parse(self, response: str, viewport: tuple[int, int]) -> AdapterResult:
        """``viewport`` is (width, height) px. Raise ParseError on bad output."""
        raise NotImplementedError
