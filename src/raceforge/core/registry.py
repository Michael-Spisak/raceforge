"""Registry of top-level documents and their schema migrations (spec 0001)."""

from collections.abc import Callable
from typing import Any

from raceforge.core.base import CoreModel

JsonDict = dict[str, Any]
Migration = Callable[[JsonDict], JsonDict]

_documents: dict[tuple[str, int], type[CoreModel]] = {}
_current: dict[str, int] = {}
_migrations: dict[tuple[str, int], Migration] = {}


def register_document[M: CoreModel](schema: str, version: int) -> Callable[[type[M]], type[M]]:
    """Class decorator: register ``model`` as document ``schema`` at ``version`` (latest wins)."""

    def decorator(model: type[M]) -> type[M]:
        key = (schema, version)
        if key in _documents and _documents[key] is not model:
            raise ValueError(f"document {schema} v{version} already registered")
        _documents[key] = model
        _current[schema] = max(_current.get(schema, 0), version)
        return model

    return decorator


def register_migration(schema: str, from_version: int) -> Callable[[Migration], Migration]:
    """Decorator: register a one-step migration of ``schema`` from ``from_version``."""

    def decorator(fn: Migration) -> Migration:
        key = (schema, from_version)
        if key in _migrations:
            raise ValueError(f"migration {schema} v{from_version} already registered")
        _migrations[key] = fn
        return fn

    return decorator


def current_version(schema: str) -> int:
    try:
        return _current[schema]
    except KeyError:
        raise ValueError(f"unknown document schema {schema!r}") from None


def model_for(schema: str, version: int) -> type[CoreModel]:
    try:
        return _documents[(schema, version)]
    except KeyError:
        raise ValueError(f"no model registered for {schema} v{version}") from None


def migrate(data: JsonDict) -> JsonDict:
    """Upgrade a raw document step by step to the current schema version."""
    schema = data.get("schema")
    version = data.get("schema_version")
    if not isinstance(schema, str) or not isinstance(version, int):
        raise ValueError("document must contain 'schema' (str) and 'schema_version' (int)")
    target = current_version(schema)
    if version > target:
        raise ValueError(f"{schema} v{version} is newer than supported v{target}; update RaceForge")
    while version < target:
        step = _migrations.get((schema, version))
        if step is None:
            raise ValueError(f"no migration for {schema} from v{version}")
        data = step(dict(data))
        if data.get("schema_version") != version + 1:
            raise ValueError(f"migration {schema} v{version} did not bump schema_version")
        version += 1
    return data


def documents() -> dict[str, type[CoreModel]]:
    """Current model per document schema."""
    return {schema: _documents[(schema, v)] for schema, v in sorted(_current.items())}
