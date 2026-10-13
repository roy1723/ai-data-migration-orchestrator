import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.schema_utils import load_inputs
from core.analysis import analyze

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def inputs():
    return load_inputs(DATA)


@pytest.fixture(scope="session")
def facts(inputs):
    return analyze(inputs["source_schema"], inputs["target_schema"], inputs["mapping"])
