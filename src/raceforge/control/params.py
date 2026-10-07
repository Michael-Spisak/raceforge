"""Tunable controller parameters (spec 0004)."""

from pathlib import Path
from typing import Any, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic.fields import FieldInfo


def Tunable(  # noqa: N802 - reads like a type in field declarations
    default: float, min: float, max: float, step: float | None = None, description: str = ""
) -> Any:
    """Declare a parameter the tuner may change within [min, max]."""
    return Field(
        default=default,
        ge=min,
        le=max,
        description=description,
        json_schema_extra={"tunable": True, "min": min, "max": max, "step": step},
    )


class ControllerParams(BaseModel):
    """Base class for controller parameters; unknown keys and out-of-range values are rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: parameters must be a mapping")
        return cls.model_validate(data)

    @classmethod
    def tunables(cls) -> dict[str, tuple[float, float, float | None]]:
        """Name -> (min, max, step) for every field declared with :func:`Tunable`."""
        out: dict[str, tuple[float, float, float | None]] = {}
        for name, info in cls.model_fields.items():
            extra = _extra(info)
            if extra.get("tunable"):
                out[name] = (
                    float(extra["min"]),
                    float(extra["max"]),
                    cast(float | None, extra.get("step")),
                )
        return out


def _extra(info: FieldInfo) -> dict[str, Any]:
    extra = info.json_schema_extra
    return cast(dict[str, Any], extra) if isinstance(extra, dict) else {}
