"""Shared registry and type contracts for named projection operations."""

from __future__ import annotations

from typing import Any, Callable

World = dict[str, Any]
Op = Callable[[World, str, str, Any], Any]

OPS: dict[str, Op] = {"direct": lambda _world, _node, _field, value: value}


def op(name: str) -> Callable[[Op], Op]:
    def register(fn: Op) -> Op:
        if name in OPS:
            raise ValueError(f"duplicate projection op: {name}")
        OPS[name] = fn
        return fn

    return register
