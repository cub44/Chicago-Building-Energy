"""Shared paths and loaders. Tests read what the pipeline wrote; they never rebuild it."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
SITE = ROOT / "site" / "data"


def latest_snapshot() -> Path:
    """The newest raw snapshot. Not published: a public checkout skips whatever needs it."""
    snaps = sorted(d for d in RAW.iterdir() if d.is_dir() and (d / "MANIFEST.json").exists()) \
        if RAW.is_dir() else []
    if not snaps:
        pytest.skip("needs the raw snapshot, which is not published")
    return snaps[-1]


@pytest.fixture(scope="session")
def snapshot() -> Path:
    return latest_snapshot()


@pytest.fixture(scope="session")
def raw_manifest(snapshot) -> dict:
    return json.loads((snapshot / "MANIFEST.json").read_text())


def need(path: Path):
    """Interim files are built from the raw snapshot, which is not published: a checkout
    without the snapshot skips every test that needs them."""
    if not path.exists():
        pytest.skip(f"{path.relative_to(ROOT)} not built: needs the raw snapshot, which is not published")
    return path


@pytest.fixture(scope="session")
def energy_long():
    import pandas as pd
    return pd.read_parquet(need(INTERIM / "energy_long.parquet"))


@pytest.fixture(scope="session")
def buildings_interim():
    import pandas as pd
    return pd.read_parquet(need(INTERIM / "buildings.parquet"))


@pytest.fixture(scope="session")
def footprints():
    import geopandas as gpd
    return gpd.read_parquet(need(INTERIM / "footprints.parquet"))


@pytest.fixture(scope="session")
def normalize_summary() -> dict:
    return json.loads(need(INTERIM / "normalize_summary.json").read_text())


@pytest.fixture(scope="session")
def match_summary() -> dict:
    return json.loads(need(INTERIM / "match_summary.json").read_text())


def read_csv(name: str):
    """A published file exactly as written: every value a string, empty cells empty."""
    import pandas as pd
    return pd.read_csv(need(PROCESSED / name), dtype=str, keep_default_na=False)


@pytest.fixture(scope="session")
def buildings_csv():
    return read_csv("buildings.csv")


@pytest.fixture(scope="session")
def energy_csv():
    return read_csv("energy.csv")
