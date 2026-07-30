"""Adapter registry. Default is ``uitars`` (preserves the flexible_parser path).

Add a family: write an ``Adapter`` subclass and register it below.
"""
from .base import Adapter, AdapterResult
from .uitars import UITarsAdapter
from .qwen3vl import Qwen3VLAdapter

REGISTRY: dict[str, type[Adapter]] = {
    "uitars": UITarsAdapter,
    "qwen3vl": Qwen3VLAdapter,
}


def get_adapter(name: str | None) -> Adapter:
    if not name:
        name = "uitars"
    if name not in REGISTRY:
        raise ValueError(f"Unknown adapter {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]()


__all__ = ["Adapter", "AdapterResult", "REGISTRY", "get_adapter"]
