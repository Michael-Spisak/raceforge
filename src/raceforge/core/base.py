"""Base model configuration shared by all core schemas (spec 0001: strict, frozen, no NaN)."""

from pydantic import BaseModel, ConfigDict


class CoreModel(BaseModel):
    """Strict, immutable base for every core data contract."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        allow_inf_nan=False,
        validate_default=True,
        use_enum_values=False,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )
