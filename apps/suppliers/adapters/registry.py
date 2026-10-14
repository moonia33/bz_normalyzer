from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.suppliers.adapters.base import BaseAdapter

_ADAPTERS: dict[str, type[BaseAdapter]] = {}


def register_adapter(cls: type[BaseAdapter]) -> type[BaseAdapter]:
    if not getattr(cls, "code", None):
        raise ValueError(f"{cls.__name__} privalo turėti class atributą code")
    _ADAPTERS[cls.code] = cls
    return cls


def get_adapter(code: str) -> type[BaseAdapter] | None:
    return _ADAPTERS.get(code)


def list_adapters() -> dict[str, type[BaseAdapter]]:
    return dict(_ADAPTERS)
