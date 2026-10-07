import pytest

import raceforge
from raceforge.cli import main


def test_version_is_set() -> None:
    assert raceforge.__version__


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert raceforge.__version__ in capsys.readouterr().out
