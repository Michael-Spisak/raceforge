"""Canonical JSON/YAML serialisation, loading with migration, content hashing, schema export."""

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from raceforge.core import assembly, parts, telemetry, track
from raceforge.core.base import CoreModel
from raceforge.core.registry import JsonDict, current_version, documents, migrate, model_for

# Importing the document modules registers them with the registry.
_DOCUMENT_MODULES = (assembly, parts, telemetry, track)


def to_jsonable(model: CoreModel) -> JsonDict:
    return model.model_dump(mode="json", by_alias=True)


def canonical_json(data: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8, no NaN/Infinity."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def dump(model: CoreModel) -> str:
    """Canonical JSON string of a model (used for API, DB, bundles and hashing)."""
    return canonical_json(to_jsonable(model))


def dump_fast(model: CoreModel) -> str:
    """Fast, non-canonical JSON for hot paths such as telemetry streaming."""
    return model.model_dump_json(by_alias=True)


def content_hash(model: CoreModel) -> str:
    """SHA-256 hex digest of the canonical JSON of a model's content."""
    return hashlib.sha256(dump(model).encode("utf-8")).hexdigest()


def _as_dict(data: Mapping[str, Any] | str | bytes) -> JsonDict:
    if isinstance(data, str | bytes):
        parsed: Any = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("document must be a JSON object")
        return cast(JsonDict, parsed)
    return dict(data)


def load(data: Mapping[str, Any] | str | bytes) -> CoreModel:
    """Load any registered document, migrating old schema versions, and validate strictly."""
    raw = migrate(_as_dict(data))
    model = model_for(cast(str, raw["schema"]), cast(int, raw["schema_version"]))
    return model.model_validate(raw)


def load_as[M: CoreModel](model_cls: type[M], data: Mapping[str, Any] | str | bytes) -> M:
    """Like :func:`load` but asserts the resulting type."""
    result = load(data)
    if not isinstance(result, model_cls):
        raise TypeError(f"expected {model_cls.__name__}, got {type(result).__name__}")
    return result


class _YamlDumper(yaml.SafeDumper):
    """Keeps umlauts readable but double-quotes strings with YAML line breaks or control chars."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    unsafe = any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "\u2028\u2029" for ch in value)
    node = dumper.represent_scalar(  # pyright: ignore[reportUnknownMemberType]
        "tag:yaml.org,2002:str", value, style='"' if unsafe else None
    )
    return node


_YamlDumper.add_representer(str, _represent_str)


def dump_yaml(model: CoreModel) -> str:
    """Human-friendly YAML (for configs people edit); round-trips losslessly."""
    return yaml.dump(to_jsonable(model), Dumper=_YamlDumper, sort_keys=False, allow_unicode=True)


def load_yaml(text: str) -> CoreModel:
    parsed: Any = yaml.safe_load(text)
    if not isinstance(parsed, dict):
        raise ValueError("YAML document must be a mapping")
    return load(cast(JsonDict, parsed))


def json_schemas() -> dict[str, JsonDict]:
    """JSON Schema of every current top-level document, keyed by ``<schema>.v<version>``."""
    return {
        f"{schema}.v{current_version(schema)}": model.model_json_schema(by_alias=True)
        for schema, model in documents().items()
    }


def export_json_schemas(directory: Path) -> list[Path]:
    """Write ``<schema>.v<version>.schema.json`` files; returns the written paths."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in json_schemas().items():
        path = directory / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written
