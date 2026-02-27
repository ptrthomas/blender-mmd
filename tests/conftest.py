from __future__ import annotations

from pathlib import Path

import pytest

from blender_mmd.pmx.parser import parse
from blender_mmd.pmx.types import Model

SAMPLES_DIR = Path(__file__).parent / "samples"


@pytest.fixture
def sample_dir() -> Path:
    return SAMPLES_DIR


@pytest.fixture
def pmx_files() -> list[Path]:
    files = sorted(SAMPLES_DIR.glob("*.pmx"))
    assert files, f"No PMX files in {SAMPLES_DIR}"
    return files


@pytest.fixture
def parsed_model(pmx_files) -> Model:
    """Parse the first sample file."""
    return parse(pmx_files[0])
