"""Local Construct settings (spec 0016): part prices, size/weight limits, budget."""

from pathlib import Path

from raceforge.api.models import ConstructSettings


def settings_path() -> Path:
    from raceforge.api.workspace import default_root

    return default_root().parent / "construct.json"


def load_settings(path: Path | None = None) -> ConstructSettings:
    p = path or settings_path()
    if not p.is_file():
        return ConstructSettings()
    return ConstructSettings.model_validate_json(p.read_text(encoding="utf-8"))


def save_settings(s: ConstructSettings, path: Path | None = None) -> ConstructSettings:
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(s.model_dump_json(indent=1), encoding="utf-8")
    tmp.replace(p)
    return s
