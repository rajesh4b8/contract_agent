"""Deferred construction for module-level service singletons.

Several modules expose a ready-to-use singleton at import time (the Neo4j
connection, the Gemini embedding client). That makes importing anything in the
dependency chain require a live database and a configured API key, which in turn
makes the code untestable offline: pytest fails during collection, before a
single test runs.

``LazyProxy`` keeps the existing ``from ... import graph`` call sites working
unchanged while moving the actual construction to first use. Import is free;
the first attribute access pays the cost.
"""
from typing import Any, Callable


class LazyProxy:
    """Stands in for an object that should not be built until it is used.

    Forwards attribute access to the real object, constructing it on the first
    such access and caching it thereafter. Only attribute access is proxied,
    which is all the singletons in this package are ever used for.
    """

    __slots__ = ("_factory", "_name", "_instance")

    def __init__(self, factory: Callable[[], Any], name: str) -> None:
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_instance", None)

    def _resolve(self) -> Any:
        instance = object.__getattribute__(self, "_instance")
        if instance is None:
            factory = object.__getattribute__(self, "_factory")
            instance = factory()
            object.__setattr__(self, "_instance", instance)
        return instance

    def __getattr__(self, item: str) -> Any:
        return getattr(self._resolve(), item)

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self._resolve(), key, value)

    def __repr__(self) -> str:
        instance = object.__getattribute__(self, "_instance")
        name = object.__getattribute__(self, "_name")
        state = "unresolved" if instance is None else repr(instance)
        return f"<LazyProxy {name}: {state}>"
