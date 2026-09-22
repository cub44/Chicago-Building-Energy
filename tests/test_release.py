"""Stages 3 and 4: what the release and the map contract promise, checked on the files as written."""
import hashlib
import json
import re

import pytest

import build
import export_site
import schema
from conftest import INTERIM, PROCESSED, ROOT, SITE, latest_snapshot, need, read_csv

WEBSITE_MAP = ROOT.parent / "connorblandford-website" / "projects" / "chicago-building-energy" / "site" / "index.html"


# --- Years ------------------------------------------------------------------------------------------------
def test_no_published_row_is_from_an_excluded_year(energy_csv):
    display = {str(y) for y in schema.DISPLAY_YEARS}
    assert set(energy_csv["data_year"]) == display
    for name in ("density_hex.csv", "density_ca.csv"):
        assert set(read_csv(name)["data_year"]) <= display, name
    classes = json.loads((PROCESSED / "classes.json").read_text())
    for m in classes["metrics"].values():
        assert set(m["years"]) == display


def test_the_map_lists_the_display_years_only():
    man = json.loads(need(SITE / "manifest.json").read_text())
    assert man["years"] == schema.DISPLAY_YEARS
    assert sorted(p.name for p in SITE.glob("values_*.json")) == [f"values_{y}.json" for y in schema.DISPLAY_YEARS]


# --- Density ----------------------------------------------------------------------------------------------
def included(energy_csv, buildings_csv):
    e = energy_csv.merge(buildings_csv[["id", "located_by"]], on="id")
    return e[(e["status"] == "submitted") & (e["site_energy_kbtu"] != "") & (e["located_by"] != "none")]


def test_hexagons_areas_and_properties_sum_to_the_same_total(energy_csv, buildings_csv):
    hexes, areas = read_csv("density_hex.csv"), read_csv("density_ca.csv")
    inc = included(energy_csv, buildings_csv)
    for y in schema.DISPLAY_YEARS:
        want = inc.loc[inc["data_year"] == str(y), "site_energy_kbtu"].astype("int64").sum()
        for frame in (hexes, areas):
            got = frame.loc[(frame["data_year"] == str(y)) & (frame["site_energy_kbtu"] != ""), "site_energy_kbtu"]
            assert got.astype("int64").sum() == want


def test_community_area_num_reproduces_the_area_table(energy_csv, buildings_csv):
    """The clean area number is the density table's own assignment: group on it and the table
    comes back exactly. Group on the free-text community_area and it does not."""
    inc = included(energy_csv, buildings_csv)
    areas = read_csv("density_ca.csv")
    for y in schema.DISPLAY_YEARS:
        got = inc[inc["data_year"] == str(y)].groupby("community_area_num")["site_energy_kbtu"].apply(
            lambda s: s.astype("int64").sum())
        want = areas[(areas["data_year"] == str(y)) & (areas["site_energy_kbtu"] != "")].set_index(
            "community_area_num")["site_energy_kbtu"].astype("int64")
        assert got.sort_index().to_dict() == want.sort_index().to_dict()
    assert energy_csv["community_area"].nunique() > 77


def test_every_located_property_has_an_area_number(buildings_csv):
    b = buildings_csv
    assert (b.loc[b["located_by"] != "none", "community_area_num"] != "").all()
    assert (b.loc[b["located_by"] == "none", "community_area_num"] == "").all()


def test_density_columns_say_area_not_land():
    for name in ("density_hex.csv", "density_ca.csv"):
        cols = read_csv(name).columns
        assert "area_sqmi" in cols and "land_sqmi" not in cols


# --- Naming (MAP_SPEC §6) ---------------------------------------------------------------------------------
def copy_to_check():
    files = [p for p in PROCESSED.glob("*") if p.is_file()]
    files += [p for p in (SITE / "caveats.json", SITE / "manifest.json", WEBSITE_MAP) if p.exists()]
    return files


def test_no_ranking_language():
    for p in copy_to_check():
        text = p.read_text(encoding="utf-8").lower()
        for w in schema.BANNED_FRAGMENTS:
            assert not re.search(rf"\b{w}s?\b", text), (p.name, w)


@pytest.mark.parametrize("name, ptype, shown", [
    ("Chicago Board Options Exchange", None, True),       # no type: a name with no owner token shows
    ("Bogan HS -CPS", None, True),
    ("108 North State Street, LLC", None, False),         # no type: owner tokens are withheld
    ("Watergate East Condo Association", None, False),
    ("1100 N Lake Shore Condominium Assn", None, False),
    ("Woodbridge Nursing Pavilion, Ltd", None, False),
    ("Kennedy Plaza BK, L.L.C.", None, False),
    ("Presence SMEMC St Elizabeth Campus", "Hospital (General Medical & Surgical)", True),
    ("Marina Towers", "Multifamily Housing", True),
    ("Jane Doe", "Multifamily Housing", False),           # residential: a personal name is withheld
    ("Lakeview Holdings LLC", "Multifamily Housing", False),
])
def test_name_policy(name, ptype, shown):
    assert (export_site.shown_name(name, ptype) is not None) == shown


def test_no_owner_token_is_shown_on_an_untyped_property():
    v = json.loads(need(SITE / f"values_{schema.DISPLAY_YEARS[-1]}.json").read_text())
    f = {k: i for i, k in enumerate(v["properties"]["fields"])}
    for r in v["properties"]["rows"]:
        if r[f["type"]] is None and r[f["name"]]:
            assert not export_site.OWNER_TOKENS.search(r[f["name"]]), r[f["name"]]


# --- The map's counts add up ------------------------------------------------------------------------------
def test_status_counts_sum_to_the_covered_total():
    c = json.loads(need(SITE / "manifest.json").read_text())["counts"]
    for y in schema.DISPLAY_YEARS:
        parts = sum(c[f"{k}_{y}"] for k in ("submitted", "not_submitted", "exempt", "not_covered"))
        assert parts == c[f"properties_{y}"]
        drawn = c[f"drawn_as_footprint_{y}"] + c[f"drawn_as_marker_{y}"] + c[f"not_drawn_{y}"]
        assert drawn == c[f"properties_{y}"]


# --- Release ----------------------------------------------------------------------------------------------
def test_checksums_cover_exactly_the_processed_files():
    # The build writes bare filenames beside the data; the published release writes the same
    # digests at the repo root, rooted at data/processed/. Whichever is here must hold.
    manifest = PROCESSED / "checksums.sha256"
    if not manifest.is_file():
        manifest = need(ROOT / "checksums.sha256")
    lines = [l.split(maxsplit=1) for l in manifest.read_text().splitlines() if l.strip()]
    listed = {name.strip().rsplit("/", 1)[-1]: digest for digest, name in lines}
    present = {p.name for p in PROCESSED.glob("*") if p.is_file() and p.name != "checksums.sha256"}
    assert set(listed) == present
    for name, digest in listed.items():
        assert hashlib.sha256((PROCESSED / name).read_bytes()).hexdigest() == digest, name


def test_the_map_manifest_names_the_snapshot_it_was_built_from():
    man = json.loads(need(SITE / "manifest.json").read_text())
    assert man["snapshot_date"] == latest_snapshot().name
    for name, meta in man["files"].items():
        assert hashlib.sha256((SITE / name).read_bytes()).hexdigest() == meta["sha256"], name


# --- Determinism ------------------------------------------------------------------------------------------
def test_stage_3_is_byte_identical_on_rebuild(tmp_path, monkeypatch):
    """Rebuild data/processed/ and the reconciliation from data/interim/ into a scratch folder and
    compare bytes. `make check` does the same from the raw snapshot with git diff."""
    interim = tmp_path / "interim"
    interim.mkdir()
    for p in INTERIM.glob("*.*"):
        if p.name not in ("property_points.parquet", "precision.json"):
            (interim / p.name).symlink_to(p)
    monkeypatch.setattr(build, "INTERIM", interim)
    monkeypatch.setattr(build, "PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(build, "RECONCILIATION", tmp_path / "reconciliation.md")
    need(INTERIM / "buildings.parquet")
    build.build_processed()
    for p in sorted(PROCESSED.glob("*")):
        assert (tmp_path / "processed" / p.name).read_bytes() == p.read_bytes(), p.name
    assert (tmp_path / "reconciliation.md").read_bytes() == (ROOT / "data" / "reconciliation.md").read_bytes()
    assert sorted(x.name for x in (tmp_path / "processed").glob("*")) == sorted(x.name for x in PROCESSED.glob("*"))


# --- Precision ---------------------------------------------------------------------------------------------
def test_the_precision_sheet_is_complete_and_current(buildings_csv):
    """Every sampled match has a verdict, and each verdict judged the match the build now makes:
    a precision published on a stale review would describe a matcher that no longer exists."""
    sheet = read_csv("match_review.csv")
    assert set(sheet["verdict"]) <= {"correct", "partial", "wrong", "unsure"} and (sheet["verdict"] != "").all()
    cur = buildings_csv.set_index("id")
    for r in sheet.itertuples():
        assert (cur.loc[r.id, "match_method"], cur.loc[r.id, "footprint_ids"]) == (r.match_method, r.footprint_ids), r.id
    assert "override" not in set(sheet["match_method"])
    per = sheet.groupby("match_method").size()
    assert (per <= 100).all()
