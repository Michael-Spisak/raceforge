import pytest

from raceforge.parts.catalogue import Catalogue


@pytest.fixture(scope="session")
def cat() -> Catalogue:
    return Catalogue.load()
